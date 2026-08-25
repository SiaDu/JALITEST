"""Parser and validators for the non-XML HCI semantic proposal format."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from expregaze_jali.transcript_anchor_model import TranscriptAnchorModel, speaker_key


REQUIRED_FIELDS = (
    "start", "intent", "affect", "heart", "gaze", "head", "lid", "blink",
    "blink_suppression",
)
HEAD_VALUES = {"NONE": 0.0, "LOW": 0.25, "MEDIUM": 0.5, "HIGH": 0.75, "FULL": 1.0}
LID_VALUES = {-9, -7, -5, -3, -2, -1, 0, 1, 2, 3, 4}
BLINK_VALUES = {"NONE", "SLOW_BLINK", "EYE_CLOSE_HOLD", "DOUBLE_BLINK", "BLINK_CLUSTER"}
SUPPRESSION_VALUES = {"NONE", "SUPPRESS"}
DIRECTION_TARGETS = {
    "DOWN", "DOWN_LEFT", "DOWN_RIGHT", "UP", "UP_LEFT", "UP_RIGHT", "LEFT", "RIGHT"
}
_SECTION = re.compile(r"^\[(ANALYZE|PERFORMANCE|REASONS)\]\s*$", re.IGNORECASE)
_PROPOSAL_ID = re.compile(r"^S\d+$", re.IGNORECASE)
_FIELD = re.compile(r"^([a-z_]+)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_START_ANCHOR = re.compile(r"^w\d{4,}$", re.IGNORECASE)
_REASON = re.compile(r"^(S\d+)\.([a-z_]+)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_INTENT_LABEL_REASON = re.compile(r"^[A-Z][A-Z0-9_']+$")
_DIGIT_INTENSITY = re.compile(r"^\d+%?$")
_INTEGRAL_DECIMAL_INTENSITY = re.compile(r"^\d+\.0+$")
_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS_WORDS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEMANTIC_VOCABULARY_PATH = REPO_ROOT / "configs" / "semantic_vocabulary.json"


class ProposalValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SemanticVocabulary:
    affect_states: dict[str, str]
    heart_states: dict[str, str]


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def load_semantic_vocabulary(
    path: str | Path = DEFAULT_SEMANTIC_VOCABULARY_PATH,
) -> SemanticVocabulary:
    """Load the dependency-free shared HCI executable vocabulary artifact."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != "semantic_vocabulary_v1":
        raise ValueError("Semantic vocabulary schema_version must be semantic_vocabulary_v1.")
    visible = data.get("visible_affect")
    heart = data.get("heart")
    if not isinstance(visible, list) or not isinstance(heart, list):
        raise ValueError("Semantic vocabulary must contain visible_affect and heart lists.")
    if not all(isinstance(name, str) and name.strip() for name in visible + heart):
        raise ValueError("Semantic vocabulary values must be non-empty strings.")
    return SemanticVocabulary(
        affect_states={_name_key(name): name for name in visible},
        heart_states={
            _name_key(name): name for name in heart
        },
    )


def normalize_intent(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
    if not normalized or normalized == "NONE":
        raise ValueError("intent must be a non-NONE actor-readable value")
    return normalized


def parse_intensity(value: str) -> int:
    """Parse a deterministic 0--100 intensity spelling without guessing."""
    raw = value.strip()
    if _DIGIT_INTENSITY.fullmatch(raw):
        return int(raw.removesuffix("%"))
    if _INTEGRAL_DECIMAL_INTENSITY.fullmatch(raw):
        return int(raw.partition(".")[0])

    words = re.sub(r"[-\s]+", " ", raw.lower()).strip().split()
    if len(words) == 1:
        if words[0] in _NUMBER_WORDS:
            return _NUMBER_WORDS[words[0]]
        if words[0] in _TENS_WORDS:
            return _TENS_WORDS[words[0]]
    elif len(words) == 2:
        if words == ["one", "hundred"]:
            return 100
        tens = _TENS_WORDS.get(words[0])
        unit = _NUMBER_WORDS.get(words[1])
        if tens is not None and unit is not None and 1 <= unit <= 9:
            return tens + unit
    raise ValueError(f'Invalid intensity "{value.strip()}"')


def _intent_reason_looks_like_label(value: str) -> bool:
    """Identify label-like intent rationale without treating it as execution."""
    return bool(_INTENT_LABEL_REASON.fullmatch(value.strip()) and "_" in value)


def _normalize_affect(
    value: str, *, phrase_id: str, field: str, vocabulary: dict[str, str]
) -> str:
    raw = value.strip()
    if raw.upper() in {"NONE", "NOTHING"}:
        return "NONE"
    state, separator, intensity_text = raw.partition("-")
    if not separator or not state.strip() or not intensity_text.strip():
        raise ProposalValidationError(f'{phrase_id}: Invalid {field} value "{raw}"')
    try:
        intensity = parse_intensity(intensity_text)
    except ValueError as exc:
        raise ProposalValidationError(f'{phrase_id}: Invalid {field} value "{raw}"') from exc
    if _name_key(state) == "nothing":
        if intensity == 0:
            return "NONE"
        raise ProposalValidationError(
            f'{phrase_id}: {field} value "{raw}" is invalid; use NONE for an inactive channel'
        )
    canonical = vocabulary.get(_name_key(state))
    if canonical is None:
        raise ProposalValidationError(f'{phrase_id}: Unknown {field} state "{state}"')
    if not 0 <= intensity <= 100:
        raise ProposalValidationError(f"{phrase_id}: {field} intensity must be between 0 and 100")
    return f"{canonical}-{intensity}"


def _normalize_gaze(value: str, *, phrase_id: str) -> str:
    raw = re.sub(r"\s+", "_", value.strip()).upper()
    if raw == "NONE":
        return raw
    # Bare AVERT means social eye-contact avoidance, but its counterpart can
    # only be determined once the transcript aliases are available. Keep it as
    # a temporary semantic value; GAZE and GLANCE still require a target.
    if raw == "AVERT":
        return raw
    mode, separator, target = raw.partition("-")
    if mode not in {"GAZE", "GLANCE", "AVERT"} or not separator or not target:
        raise ProposalValidationError(f'{phrase_id}: Invalid gaze value "{value.strip()}"')
    # Character names are validated only after the immutable transcript has
    # supplied its speakers and A/B aliases. At this stage validate syntax, not
    # membership, so e.g. GAZE-WILL can reach that character-aware stage.
    if not re.fullmatch(r"(?:CHARACTER_)?[A-Z0-9]+(?:_[A-Z0-9]+)*", target):
        raise ProposalValidationError(f'{phrase_id}: Invalid gaze target "{target}"')
    return f"{mode}-{target}"


def _normalize_phrase(
    phrase: dict[str, str], *, vocabulary: SemanticVocabulary
) -> dict[str, Any]:
    phrase_id = phrase["proposal_id"]
    missing = [field for field in REQUIRED_FIELDS if field not in phrase]
    if missing:
        raise ProposalValidationError(f"{phrase_id}: Missing required fields: {', '.join(missing)}")
    start_anchor = phrase["start"].strip().lower()
    if _START_ANCHOR.fullmatch(start_anchor) is None:
        raise ProposalValidationError(f'{phrase_id}: Invalid start anchor "{phrase["start"]}"')
    head = phrase["head"].strip().upper()
    if head not in HEAD_VALUES:
        raise ProposalValidationError(f'{phrase_id}: Unknown head value "{phrase["head"]}"')
    lid_raw = phrase["lid"].strip().upper()
    if lid_raw == "NONE":
        lid: int | None = None
    else:
        try:
            lid = int(lid_raw)
        except ValueError as exc:
            raise ProposalValidationError(f'{phrase_id}: Invalid lid value "{phrase["lid"]}"') from exc
        if lid not in LID_VALUES:
            raise ProposalValidationError(f"{phrase_id}: Unsupported lid value {lid}")
    blink = phrase["blink"].strip().upper()
    if blink not in BLINK_VALUES:
        raise ProposalValidationError(f'{phrase_id}: Unknown blink value "{phrase["blink"]}"')
    suppression = phrase["blink_suppression"].strip().upper()
    if suppression not in SUPPRESSION_VALUES:
        raise ProposalValidationError(
            f'{phrase_id}: Unknown blink_suppression value "{phrase["blink_suppression"]}"'
        )
    try:
        intent = normalize_intent(phrase["intent"])
    except ValueError as exc:
        raise ProposalValidationError(f"{phrase_id}: {exc}") from exc
    return {
        "proposal_id": phrase_id,
        "start_anchor": start_anchor,
        "intent": intent,
        "affect": _normalize_affect(
            phrase["affect"], phrase_id=phrase_id, field="affect", vocabulary=vocabulary.affect_states
        ),
        "heart": _normalize_affect(
            phrase["heart"], phrase_id=phrase_id, field="heart", vocabulary=vocabulary.heart_states
        ),
        "gaze": _normalize_gaze(phrase["gaze"], phrase_id=phrase_id),
        "head": head,
        "lid": lid,
        "blink": blink,
        "blink_suppression": suppression,
    }


def parse_performance_proposal(
    source: str | Path, *, vocabulary: SemanticVocabulary
) -> dict[str, Any]:
    """Parse and semantically normalize one line-oriented model proposal."""
    text = Path(source).read_text(encoding="utf-8") if isinstance(source, Path) else str(source)
    sections: dict[str, list[str]] = {"ANALYZE": [], "PERFORMANCE": [], "REASONS": []}
    current: str | None = None
    seen: list[str] = []
    for line in text.splitlines():
        section_match = _SECTION.fullmatch(line.strip())
        if section_match:
            current = section_match.group(1).upper()
            if current in seen:
                raise ProposalValidationError(f"Duplicate [{current}] section")
            seen.append(current)
            continue
        if current is not None:
            sections[current].append(line)
        elif line.strip():
            raise ProposalValidationError("Text before [ANALYZE] is not allowed")
    missing_sections = [name for name in sections if name not in seen]
    if missing_sections:
        raise ProposalValidationError(f"Missing required sections: {', '.join(missing_sections)}")
    if seen != ["ANALYZE", "PERFORMANCE", "REASONS"]:
        raise ProposalValidationError("Sections must be ordered [ANALYZE], [PERFORMANCE], [REASONS]")

    raw_phrases: list[dict[str, str]] = []
    current_phrase: dict[str, str] | None = None
    for line in sections["PERFORMANCE"]:
        stripped = line.strip()
        if not stripped:
            continue
        if _PROPOSAL_ID.fullmatch(stripped):
            current_phrase = {"proposal_id": stripped.upper()}
            raw_phrases.append(current_phrase)
            continue
        field_match = _FIELD.fullmatch(stripped)
        if current_phrase is None or field_match is None:
            raise ProposalValidationError(f"Malformed [PERFORMANCE] line: {stripped}")
        field, value = field_match.groups()
        field = field.lower()
        if field not in REQUIRED_FIELDS:
            raise ProposalValidationError(f"{current_phrase['proposal_id']}: Unknown field {field}")
        if field in current_phrase:
            raise ProposalValidationError(f"{current_phrase['proposal_id']}: Duplicate field {field}")
        current_phrase[field] = value
    if not raw_phrases:
        raise ProposalValidationError("[PERFORMANCE] must contain at least one phrase")
    ids = [phrase["proposal_id"] for phrase in raw_phrases]
    if len(ids) != len(set(ids)):
        raise ProposalValidationError("Proposal phrase IDs must be unique")
    phrases = [_normalize_phrase(phrase, vocabulary=vocabulary) for phrase in raw_phrases]

    reasons: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    current_reason_phrase: str | None = None

    def store_reason(phrase_id: str, field: str, reason: str) -> None:
        if phrase_id not in ids:
            raise ProposalValidationError(f"Reason refers to unknown phrase {phrase_id}")
        if field not in REQUIRED_FIELDS[1:]:
            raise ProposalValidationError(f"{phrase_id}: Reason refers to unknown field {field}")
        destination = reasons.setdefault(phrase_id, {})
        if field in destination:
            raise ProposalValidationError(f"{phrase_id}: Duplicate reason for {field}")
        destination[field] = reason
        if field == "intent" and _intent_reason_looks_like_label(reason):
            warnings.append(
                f"{phrase_id}: intent rationale looks like a label rather than an explanation"
            )

    for line in sections["REASONS"]:
        stripped = line.strip()
        if not stripped:
            continue
        if _PROPOSAL_ID.fullmatch(stripped):
            current_reason_phrase = stripped.upper()
            if current_reason_phrase not in ids:
                raise ProposalValidationError(
                    f"Reason refers to unknown phrase {current_reason_phrase}"
                )
            continue
        match = _REASON.fullmatch(stripped)
        if match is not None:
            phrase_id, field, reason = match.groups()
            store_reason(phrase_id.upper(), field.lower(), reason)
            continue
        field_match = _FIELD.fullmatch(stripped)
        if field_match is None or current_reason_phrase is None:
            raise ProposalValidationError(f"Malformed [REASONS] line: {stripped}")
        field, reason = field_match.groups()
        if not reason:
            raise ProposalValidationError(f"Malformed [REASONS] line: {stripped}")
        store_reason(current_reason_phrase, field.lower(), reason)
    for phrase in phrases:
        phrase_reasons = reasons.get(phrase["proposal_id"], {})
        for field in REQUIRED_FIELDS[1:]:
            value = phrase[field]
            active = (
                field == "intent"
                or (field == "head" and value != "NONE")
                or (field not in {"intent", "head", "lid"} and value not in ("NONE", None))
            )
            if active and field not in phrase_reasons:
                warnings.append(f"{phrase['proposal_id']}: missing rationale for {field}")
    return {
        "analyze": "\n".join(sections["ANALYZE"]).strip("\n"),
        "phrases": phrases,
        "reasons": reasons,
        "diagnostics": {"errors": [], "warnings": warnings},
    }


def validate_proposal_anchors(
    proposal: dict[str, Any], anchor_model: TranscriptAnchorModel
) -> list[dict[str, Any]]:
    """Validate phrase starts and deterministically partition target turns."""
    anchors = list(anchor_model.anchors)
    by_id = {anchor.anchor_id: (index, anchor) for index, anchor in enumerate(anchors)}
    turns = {turn.turn_id: turn for turn in anchor_model.turns}
    target_key = speaker_key(anchor_model.target_character)
    resolved: list[dict[str, Any]] = []
    seen_starts: set[str] = set()
    previous_index: int | None = None

    for phrase in proposal.get("phrases", []):
        phrase_id = str(phrase.get("proposal_id", "<unknown>"))
        start_id = phrase.get("start_anchor")
        if start_id not in by_id:
            raise ProposalValidationError(f"{phrase_id}: unknown anchor {start_id}")
        start_index, start = by_id[start_id]
        if speaker_key(start.speaker) != target_key:
            raise ProposalValidationError(f"{phrase_id}: phrase start belongs to another character's dialogue")
        if start_id in seen_starts:
            raise ProposalValidationError(f"{phrase_id}: duplicate phrase boundary {start_id}")
        if previous_index is not None and start_index < previous_index:
            raise ProposalValidationError(f"{phrase_id}: phrase boundaries are not in transcript order")
        seen_starts.add(start_id)
        row = {**phrase, "turn_id": start.turn_id, "start_index": start_index}
        resolved.append(row)
        previous_index = start_index

    for turn in anchor_model.turns:
        if speaker_key(turn.speaker) != target_key or not turn.anchors:
            continue
        turn_phrases = [phrase for phrase in resolved if phrase["turn_id"] == turn.turn_id]
        if not turn_phrases:
            raise ProposalValidationError(f"Target turn {turn.turn_id} has no Performance Phrase")
        first_anchor = turn.anchors[0].anchor_id
        if turn_phrases[0]["start_anchor"] != first_anchor:
            raise ProposalValidationError(
                f"{turn.turn_id}: first Performance Phrase must start at {first_anchor}"
            )

    for index, phrase in enumerate(resolved):
        start = anchors[phrase["start_index"]]
        next_phrase = resolved[index + 1] if index + 1 < len(resolved) else None
        if next_phrase is not None and next_phrase["turn_id"] == phrase["turn_id"]:
            char_end = anchors[next_phrase["start_index"]].char_start
        else:
            char_end = turns[phrase["turn_id"]].utterance_end
        phrase["char_start"] = start.char_start
        phrase["char_end"] = char_end
        phrase["text"] = anchor_model.script[start.char_start:char_end]
        phrase.pop("start_index")
    return resolved


def validate_and_resolve_proposal_targets(
    proposal: dict[str, Any], anchor_model: TranscriptAnchorModel
) -> dict[str, Any]:
    """Resolve known dialogue-character gaze names to the proposal A/B form.

    This deliberately validates semantic transcript targets only. Maya-node
    mapping is execution/session validation and happens during animation apply.
    """
    aliases_by_character = {
        speaker_key(character): alias for alias, character in anchor_model.aliases.items()
    }
    for phrase in proposal.get("phrases", []):
        gaze = str(phrase.get("gaze") or "NONE")
        if gaze == "NONE":
            continue
        if gaze == "AVERT":
            counterpart = anchor_model.aliases.get("B")
            if not counterpart:
                raise ProposalValidationError(
                    f'{phrase["proposal_id"]}: Bare AVERT requires a known social counterpart '
                    "or an explicit direction/target."
                )
            phrase["gaze"] = "AVERT-B"
            continue
        mode, target = gaze.split("-", 1)
        if target in anchor_model.aliases or target in DIRECTION_TARGETS or target.startswith("OBJECT_"):
            continue

        character_target = target[len("CHARACTER_"):] if target.startswith("CHARACTER_") else target
        alias = aliases_by_character.get(speaker_key(character_target))
        if alias is None:
            raise ProposalValidationError(
                f'{phrase["proposal_id"]}: Unknown character gaze target "{character_target}"'
            )
        phrase["gaze"] = f"{mode}-{alias}"
    return proposal
