"""Pure-Python editable score projection for dual Performance Plan v2."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any

from expregaze_jali.dual_sparse_performance_proposal_parser import BLINK_VALUES, HEAD_VALUES
from expregaze_jali.performance_proposal_parser import load_semantic_vocabulary
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel, speaker_key

CHANNEL_ORDER = ("affect", "gaze", "head", "blink")
_TAG = re.compile(r"<([^<>\r\n]+)>")
_AFFECT = re.compile(r"^(.+)-(\d+)$")
_GAZE = re.compile(r"^(GAZE|GLANCE|AVERT)-(.+)$")


@dataclass(frozen=True)
class DisplayAnchor:
    anchor_id: str
    text: str
    speaker: str
    start: int
    end: int


@dataclass(frozen=True)
class SpeakerRange:
    speaker: str
    start: int
    end: int


@dataclass(frozen=True)
class DialogueProjection:
    display_text: str
    anchors: tuple[DisplayAnchor, ...]
    speaker_ranges: tuple[SpeakerRange, ...]

    @property
    def anchor_map(self) -> dict[str, DisplayAnchor]:
        return {anchor.anchor_id: anchor for anchor in self.anchors}


@dataclass(frozen=True)
class ScoreIssue:
    actor: str
    message: str

    def __str__(self) -> str:
        return f"{self.actor}: {self.message}"


@dataclass(frozen=True)
class ScoreValidation:
    events: tuple[dict[str, Any], ...]
    errors: tuple[ScoreIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def phrases(self) -> tuple[dict[str, Any], ...]:
        return self.events


def build_dialogue_projection(model: ConversationAnchorModel) -> DialogueProjection:
    """Project immutable dialogue without speaker prefixes."""
    chunks: list[str] = []
    anchors: list[DisplayAnchor] = []
    ranges: list[SpeakerRange] = []
    offset = 0
    for turn_index, turn in enumerate(model.turns):
        if turn_index:
            chunks.append("\n")
            offset += 1
        utterance = turn.utterance_text
        turn_start = offset
        chunks.append(utterance)
        for anchor in turn.anchors:
            relative_start = anchor.char_start - turn.utterance_start
            relative_end = anchor.char_end - turn.utterance_start
            anchors.append(DisplayAnchor(anchor.anchor_id, anchor.text, turn.speaker, offset + relative_start, offset + relative_end))
        offset += len(utterance)
        ranges.append(SpeakerRange(turn.speaker, turn_start, offset))
    return DialogueProjection("".join(chunks), tuple(anchors), tuple(ranges))


def _format_tag(channel: str, value: str) -> str:
    del channel
    return f"<{value}>"


def render_actor_score(plan: dict[str, Any], projection: DialogueProjection, actor: str) -> str:
    insertions: dict[int, list[str]] = {}
    anchors = projection.anchor_map
    for event in plan.get("tracks", {}).get(actor, []):
        anchor = anchors[event["anchor_id"]]
        before = speaker_key(anchor.speaker) == speaker_key(actor)
        offset = anchor.start if before else anchor.end
        tags = [_format_tag(channel, event["changes"][channel]) for channel in CHANNEL_ORDER if channel in event.get("changes", {})]
        insertions.setdefault(offset, []).extend(tags)
    text = projection.display_text
    for offset in sorted(insertions, reverse=True):
        text = text[:offset] + "".join(insertions[offset]) + text[offset:]
    return text


class DualSparseScoreModel:
    """Validate and apply independent actor score editors for v2 plans."""

    def __init__(self, plan: dict[str, Any], anchor_model: ConversationAnchorModel):
        if plan.get("schema_version") != "dual_performance_plan_v2":
            raise ValueError("DualSparseScoreModel requires dual_performance_plan_v2")
        self.plan = deepcopy(plan)
        self.characters = list(self.plan.get("characters", []))
        if len(self.characters) != 2 or set(self.plan.get("tracks", {})) != set(self.characters):
            raise ValueError("v2 requires two named characters and name-keyed tracks")
        self.projection = build_dialogue_projection(anchor_model)
        self.score_texts = {actor: render_actor_score(self.plan, self.projection, actor) for actor in self.characters}
        self.score_text = self.score_texts[self.characters[0]]
        self.phrases = [event for actor in self.characters for event in self.plan["tracks"][actor]]
        vocabulary = load_semantic_vocabulary()
        self.affect_states = {value.lower(): value for value in vocabulary.affect_states.values()}
        self.targets: set[str] = set()

    def _tag(self, actor: str, value: str) -> tuple[str, str] | str:
        upper = value.upper()
        if upper == "MASK-NONE":
            return "affect", "MASK-NONE"
        match = _AFFECT.fullmatch(value)
        if match and match.group(1).lower() in self.affect_states and int(match.group(2)) > 0:
            return "affect", f"{self.affect_states[match.group(1).lower()]}-{int(match.group(2))}"
        if upper == "GAZE-NONE":
            return "gaze", "GAZE-NONE"
        gaze = _GAZE.fullmatch(value)
        if gaze:
            mode, target = gaze.group(1).upper(), gaze.group(2)
            directions = {"RIGHT", "LEFT", "DOWN", "DOWN_LEFT", "DOWN_RIGHT", "UP", "UP_LEFT", "UP_RIGHT"}
            if target.upper() in directions and mode != "AVERT":
                return "Directional gaze targets require AVERT"
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_'-]*", target):
                named = next((name for name in self.characters if speaker_key(name) == speaker_key(target)), target)
                return "gaze", f"{mode}-{named if target.upper() not in directions else target.upper()}"
        if upper in HEAD_VALUES:
            return "head", upper
        if upper in BLINK_VALUES:
            return "blink", upper
        return f'Unknown or invalid v2 tag <{value}>'

    def validate_actor(self, actor: str, text: str) -> ScoreValidation:
        if actor not in self.characters:
            return ScoreValidation((), (ScoreIssue(actor, "Unknown actor editor"),))
        errors: list[ScoreIssue] = []
        placements: list[tuple[int, str, str]] = []
        stripped: list[str] = []
        cursor = 0
        for match in _TAG.finditer(text):
            plain = text[cursor:match.start()]
            stripped.append(plain)
            offset = sum(len(part) for part in stripped)
            parsed = self._tag(actor, match.group(1).strip())
            if isinstance(parsed, str):
                errors.append(ScoreIssue(actor, parsed))
            else:
                placements.append((offset, parsed[0], parsed[1]))
            cursor = match.end()
        stripped.append(text[cursor:])
        clean = "".join(stripped)
        if clean != self.projection.display_text:
            errors.append(ScoreIssue(actor, "Dialogue text is immutable; stripping valid tags must reproduce canonical display_text exactly"))
        by_start = {a.start: a for a in self.projection.anchors if speaker_key(a.speaker) == speaker_key(actor)}
        by_end = {a.end: a for a in self.projection.anchors if speaker_key(a.speaker) != speaker_key(actor)}
        grouped: dict[str, dict[str, str]] = {}
        for offset, channel, value in placements:
            anchor = by_start.get(offset) or by_end.get(offset)
            if anchor is None:
                errors.append(ScoreIssue(actor, f"Tag at display offset {offset} is not role-appropriately before a spoken word or after a heard word"))
                continue
            changes = grouped.setdefault(anchor.anchor_id, {})
            if channel in changes:
                errors.append(ScoreIssue(actor, f"Duplicate {channel} change at {anchor.anchor_id}"))
            changes[channel] = value
        events = tuple({"actor": actor, "anchor_id": anchor_id, "changes": changes} for anchor_id, changes in grouped.items())
        return ScoreValidation(events, tuple(errors))

    def validate(self, value: str | dict[str, str]) -> ScoreValidation:
        texts = value if isinstance(value, dict) else {self.characters[0]: value, self.characters[1]: self.score_texts[self.characters[1]]}
        results = [self.validate_actor(actor, texts.get(actor, "")) for actor in self.characters]
        return ScoreValidation(tuple(event for result in results for event in result.events), tuple(error for result in results for error in result.errors))

    def apply(self, value: str | dict[str, str]) -> dict[str, Any]:
        texts = value if isinstance(value, dict) else {self.characters[0]: value, self.characters[1]: self.score_texts[self.characters[1]]}
        result = self.validate(texts)
        if not result.valid:
            raise ValueError("\n".join(str(error) for error in result.errors))
        old = {(actor, event["anchor_id"]): event for actor in self.characters for event in self.plan["tracks"][actor]}
        next_id = max([int(event["event_id"][1:]) for event in old.values() if re.fullmatch(r"E\d+", event.get("event_id", ""))] or [0]) + 1
        tracks = {actor: [] for actor in self.characters}
        for row in result.events:
            prior = old.get((row["actor"], row["anchor_id"]))
            event_id = prior.get("event_id") if prior else f"E{next_id:03d}"
            if prior is None:
                next_id += 1
            tracks[row["actor"]].append({"event_id": event_id, "anchor_id": row["anchor_id"], "changes": deepcopy(row["changes"]), "reason": prior.get("reason") if prior else None})
        self.plan["tracks"] = tracks
        self.score_texts = dict(texts)
        self.score_text = self.score_texts[self.characters[0]]
        self.phrases = [event for actor in self.characters for event in tracks[actor]]
        return self.plan

    def rationale_view(self, event_number: int) -> str:
        rows = [(actor, event) for actor in self.characters for event in self.plan["tracks"][actor] if event.get("reason")]
        if not rows:
            return "No sparse change events have reasons."
        actor, event = rows[max(0, min(event_number - 1, len(rows) - 1))]
        anchor = self.projection.anchor_map[event["anchor_id"]]
        changes = "\n".join(f"{channel} -> {value}" for channel, value in event["changes"].items())
        return f'{actor} @ "{anchor.text}"\n{changes}\n\nReason:\n{event["reason"]}'
