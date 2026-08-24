"""Deterministic dialogue turns and word anchors for the HCI authoring path."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


_SPEAKER_LINE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<speaker>[A-Za-z][A-Za-z0-9 _'-]*?)[ \t]*:[ \t]*"
)
_ANCHOR = re.compile(r"\S+")
_ANNOTATION_TAG = re.compile(
    r"<\s*/?\s*(?:i\d+|g\d+|m\d+|h\d+|hd\d+|l\d+|pb\d+|bs\d+|heart\d+|mask|heart)\b[^>]*>",
    re.IGNORECASE,
)
_INLINE_UPPERCASE_SPEAKER_LABEL = re.compile(
    r"(?<=[.!?])[ \t]+(?P<speaker>[A-Z][A-Z0-9_'-]*(?:[ \t]+[A-Z][A-Z0-9_'-]*)*)[ \t]*:"
)


def speaker_key(value: str) -> str:
    """Return a stable comparison/canonical key for a dialogue character."""
    return re.sub(r"[^A-Z0-9]+", "_", str(value).strip().upper()).strip("_")


def validate_clean_dialogue_script(script: str) -> None:
    """Reject legacy performance markup without altering participant dialogue."""
    if _ANNOTATION_TAG.search(str(script)):
        raise ValueError(
            "Input Script contains performance annotation tags. Generate Performance Plan "
            "expects clean dialogue text without JALI or legacy performance tags."
        )


def _has_multiple_turns_on_line(content: str, first_match: re.Match[str]) -> bool:
    """Detect another likely screenplay speaker label after an initial label."""
    tail = content[first_match.end():]
    first_key = speaker_key(first_match.group("speaker"))
    for match in _INLINE_UPPERCASE_SPEAKER_LABEL.finditer(tail):
        if match.group("speaker"):
            return True
    repeated_name = re.compile(
        rf"(?<![A-Za-z0-9_'-]){re.escape(first_match.group('speaker').strip())}[ \t]*:",
        re.IGNORECASE,
    )
    return any(speaker_key(match.group(0).rstrip(":")) == first_key for match in repeated_name.finditer(tail))


@dataclass(frozen=True)
class AnchorUnit:
    anchor_id: str
    turn_id: str
    speaker: str
    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class DialogueTurn:
    turn_id: str
    speaker: str
    char_start: int
    char_end: int
    utterance_start: int
    utterance_end: int
    utterance_text: str
    anchors: tuple[AnchorUnit, ...]


@dataclass(frozen=True)
class TranscriptAnchorModel:
    script: str
    target_character: str
    turns: tuple[DialogueTurn, ...]
    aliases: dict[str, str]

    @property
    def anchors(self) -> tuple[AnchorUnit, ...]:
        return tuple(anchor for turn in self.turns for anchor in turn.anchors)

    def anchored_script(self) -> str:
        blocks: list[str] = []
        for turn in self.turns:
            units = " ".join(f"[{a.anchor_id} {a.text}]" for a in turn.anchors)
            blocks.append(f"{turn.turn_id} {turn.speaker}:\n{units}")
        return "\n\n".join(blocks) + "\n"

    def anchor_map(self) -> dict[str, Any]:
        return {
            "format": "transcript_anchor_v1",
            "target_character": self.target_character,
            "aliases": dict(self.aliases),
            "turns": [
                {
                    **{key: value for key, value in asdict(turn).items() if key != "anchors"},
                    "anchors": [asdict(anchor) for anchor in turn.anchors],
                }
                for turn in self.turns
            ],
            "anchors": [asdict(anchor) for anchor in self.anchors],
        }


def build_transcript_anchor_model(
    script: str, *, target_character: str
) -> TranscriptAnchorModel:
    """Parse an immutable script into labeled turns and global ``w####`` anchors."""
    source = str(script)
    target = str(target_character).strip()
    if not source.strip():
        raise ValueError("Script is required.")
    if not target:
        raise ValueError("target_character is required.")
    validate_clean_dialogue_script(source)

    lines: list[tuple[int, int, str, re.Match[str] | None]] = []
    offset = 0
    for raw_line in source.splitlines(keepends=True):
        content = raw_line.rstrip("\r\n")
        end = offset + len(content)
        if content.strip():
            lines.append((offset, end, content, _SPEAKER_LINE.match(content)))
        offset += len(raw_line)
    if offset < len(source):  # defensive; splitlines normally consumes the tail
        content = source[offset:]
        if content.strip():
            lines.append((offset, len(source), content, _SPEAKER_LINE.match(content)))

    labeled = any(match is not None for _, _, _, match in lines)
    specs: list[tuple[str, int, int, int, int]] = []
    if not labeled:
        specs.append((target, 0, len(source), 0, len(source)))
    else:
        for start, end, content, match in lines:
            if match is None:
                raise ValueError(
                    "Speaker-labeled scripts require every non-empty line to begin with "
                    "a speaker label followed by a colon."
                )
            if _has_multiple_turns_on_line(content, match):
                raise ValueError(
                    "Multiple dialogue turns were found on one line. Put each "
                    "CHARACTER: dialogue turn on its own line."
                )
            raw_speaker = match.group("speaker").strip()
            speaker = target if speaker_key(raw_speaker) == speaker_key(target) else speaker_key(raw_speaker)
            utterance_start = start + match.end()
            specs.append((speaker, start, end, utterance_start, end))

    speakers: list[str] = []
    for speaker, *_ in specs:
        if speaker_key(speaker) not in {speaker_key(item) for item in speakers}:
            speakers.append(speaker)
    if len(speakers) > 2:
        raise ValueError("The HCI prototype supports at most two dialogue characters.")
    if labeled and speaker_key(target) not in {speaker_key(item) for item in speakers}:
        raise ValueError(f"Target character {target!r} does not appear in the labeled script.")

    aliases = {"A": target}
    others = [speaker for speaker in speakers if speaker_key(speaker) != speaker_key(target)]
    if len(others) == 1:
        aliases["B"] = others[0]

    turns: list[DialogueTurn] = []
    next_anchor = 1
    for turn_number, (speaker, start, end, utterance_start, utterance_end) in enumerate(specs, 1):
        turn_id = f"T{turn_number:02d}"
        utterance = source[utterance_start:utterance_end]
        anchors: list[AnchorUnit] = []
        for match in _ANCHOR.finditer(utterance):
            char_start = utterance_start + match.start()
            char_end = utterance_start + match.end()
            anchors.append(
                AnchorUnit(
                    anchor_id=f"w{next_anchor:04d}",
                    turn_id=turn_id,
                    speaker=speaker,
                    text=source[char_start:char_end],
                    char_start=char_start,
                    char_end=char_end,
                )
            )
            next_anchor += 1
        turns.append(
            DialogueTurn(
                turn_id=turn_id,
                speaker=speaker,
                char_start=start,
                char_end=end,
                utterance_start=utterance_start,
                utterance_end=utterance_end,
                utterance_text=utterance,
                anchors=tuple(anchors),
            )
        )
    return TranscriptAnchorModel(source, target, tuple(turns), aliases)
