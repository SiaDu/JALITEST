"""Maya-independent Semantic Performance Score model.

The structured Performance Plan remains canonical.  This module provides a
human-facing projection and applies validated semantic edits without importing
Maya, Qt, or the backend package.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Iterable


GAZE_MODES = {"GAZE", "GLANCE", "AVERT"}
DEFAULT_GAZE_TARGETS = {
    "A", "B", "SPEAKER", "LISTENER", "DOWN", "DOWN_LEFT", "DOWN_RIGHT",
    "UP", "UP_LEFT", "UP_RIGHT", "LEFT", "RIGHT", "CRYSTAL", "DOOR",
}
REPO_ROOT = Path(__file__).resolve().parents[2]
SEMANTIC_VOCABULARY_PATH = REPO_ROOT / "configs" / "semantic_vocabulary.json"
BLINK_VALUES = {
    "SLOW_BLINK", "EYE_CLOSE_HOLD", "SUPPRESS", "DOUBLE_BLINK", "BLINK_CLUSTER",
}
HEAD_INVOLVEMENT = {"NONE": 0.0, "LOW": 0.25, "MEDIUM": 0.5, "HIGH": 0.75, "FULL": 1.0}
HEAD_LEVEL_BY_INVOLVEMENT = {value: key for key, value in HEAD_INVOLVEMENT.items()}
_HEADER = re.compile(r"^\s*(\d+)\.\s*(.*?)\s*$")
_INTENT = re.compile(r"^\{([A-Za-z][A-Za-z0-9_]*)\}$")
_TAG = re.compile(r"<([^<>]+)>")
_AFFECT = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)-(\d{1,3})$")
_HEART = re.compile(r"^HEART-([A-Za-z][A-Za-z0-9_]*)-(\d{1,3})$")
_HEAD = re.compile(r"^HEAD-([A-Z]+)$")
_LID = re.compile(r"^l(-?\d+(?:\.\d+)?)$", re.IGNORECASE)


def load_score_vocabulary(path: str | Path = SEMANTIC_VOCABULARY_PATH) -> tuple[set[str], set[str]]:
    """Read the shared JSON vocabulary without backend or YAML dependencies."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != "semantic_vocabulary_v1":
        raise ValueError("Semantic vocabulary schema_version must be semantic_vocabulary_v1.")
    visible, heart = data.get("visible_affect"), data.get("heart")
    if not isinstance(visible, list) or not isinstance(heart, list):
        raise ValueError("Semantic vocabulary must contain visible_affect and heart lists.")
    if not all(isinstance(name, str) and name.strip() for name in visible + heart):
        raise ValueError("Semantic vocabulary values must be non-empty strings.")
    return set(visible), set(heart)


DEFAULT_VISIBLE_AFFECTS, DEFAULT_HEART_STATES = load_score_vocabulary()


@dataclass(frozen=True)
class ValidationIssue:
    phrase_number: int | None
    message: str

    def __str__(self) -> str:
        prefix = f"Phrase {self.phrase_number}: " if self.phrase_number is not None else ""
        return prefix + self.message


@dataclass(frozen=True)
class SemanticState:
    intent: str | None = None
    lid: float | None = None
    affect: tuple[str, int] | None = None
    hidden_affect: tuple[str, int] | None = None
    gaze: tuple[str, str] | None = None
    head: str | None = None
    blinks: tuple[str, ...] = ()


@dataclass
class ScorePhrase:
    number: int
    text: str
    event_index: int
    char_start: int
    char_end: int
    states: dict[str, SemanticState]
    speaker: str = "A"
    references: dict[str, dict[str, Any] | list[dict[str, Any]] | None] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedPhrase:
    number: int
    text: str
    states: dict[str, SemanticState]
    speaker: str = "A"


@dataclass(frozen=True)
class ParseResult:
    phrases: tuple[ParsedPhrase, ...]
    errors: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class RationaleItem:
    category: str
    behavior: str
    reason: str


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _span_start(span: dict[str, Any]) -> int | None:
    try:
        return int(span.get("char_start"))
    except (TypeError, ValueError):
        return None


def _span_end(span: dict[str, Any]) -> int | None:
    try:
        return int(span.get("char_end"))
    except (TypeError, ValueError):
        return None


def _all_spans(plan: dict[str, Any], path: tuple[str, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in _as_list(plan.get("events")):
        value: Any = event
        for key in path:
            value = _as_dict(value).get(key)
        result.extend(item for item in _as_list(value) if isinstance(item, dict))
    return sorted(result, key=lambda item: (_span_start(item) if _span_start(item) is not None else 10**18))


def _active_at(spans: list[dict[str, Any]], position: int) -> dict[str, Any] | None:
    """Return the latest-starting canonical span that actually covers position."""
    candidates = [
        span
        for span in spans
        if _span_start(span) is not None
        and _span_end(span) is not None
        and int(span["char_start"]) <= position < int(span["char_end"])
    ]
    return candidates[-1] if candidates else None


def _active(spans: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    return [
        span for span in spans
        if _span_start(span) is not None and _span_end(span) is not None
        and int(span["char_start"]) < end and int(span["char_end"]) > start
    ]


def _human_target(value: str) -> str:
    target = value.strip()
    for prefix in ("CHARACTER_", "OBJECT_"):
        if target.upper().startswith(prefix):
            return target[len(prefix):].upper()
    return target.upper()


def _affect_state(span: dict[str, Any] | None) -> tuple[str, int] | None:
    if not span:
        return None
    state = str(span.get("state") or "").strip()
    intensity = _number(span.get("intensity"))
    if not state:
        match = _AFFECT.match(str(span.get("value") or "").strip())
        if match:
            state, intensity = match.group(1), int(match.group(2)) / 100.0
    if not state:
        return None
    return state, int(round((intensity or 0.0) * 100))


def _gaze_state(span: dict[str, Any] | None) -> tuple[str, str] | None:
    if not span:
        return None
    mode = str(span.get("mode") or "").strip().upper()
    target = str(span.get("target") or "").strip()
    if not mode:
        mode, separator, target_from_value = str(span.get("value") or "").partition("-")
        target = target or (target_from_value if separator else "")
    return (mode.upper(), _human_target(target)) if mode and target else None


def _lid_state(span: dict[str, Any] | None) -> float | None:
    if not span:
        return None
    return _number(span.get("lid_state", span.get("value")))


def _head_state(span: dict[str, Any] | None) -> str | None:
    if not span:
        return None
    value = str(span.get("value") or "").strip().upper()
    if value in HEAD_INVOLVEMENT:
        return value
    involvement = _number(span.get("involvement"))
    if involvement is None:
        return None
    return next(
        (level for numeric, level in HEAD_LEVEL_BY_INVOLVEMENT.items() if abs(involvement - numeric) < 1e-9),
        None,
    )


def _event_bounds(event: dict[str, Any]) -> tuple[int, int] | None:
    span = _as_dict(event.get("span"))
    try:
        return int(span["char_start"]), int(span["char_end"])
    except (KeyError, TypeError, ValueError):
        return None


def _phrase_text(event: dict[str, Any], start: int, end: int) -> str:
    span = _as_dict(event.get("span"))
    text = str(span.get("text") or "")
    bounds = _event_bounds(event)
    if bounds is None:
        return text.strip()
    event_start, event_end = bounds
    if len(text) == event_end - event_start:
        return text[max(0, start - event_start):max(0, end - event_start)].strip()
    return text.strip() if (start, end) == bounds else ""


def derive_phrases(plan: dict[str, Any], *, alias: str = "A") -> list[ScorePhrase]:
    """Derive deterministic phrases with complete resolved persistent state."""
    affect = _all_spans(plan, ("affect", "visible"))
    hidden_affect = _all_spans(plan, ("affect", "hidden"))
    gaze = _all_spans(plan, ("gaze",))
    head = _all_spans(plan, ("head",))
    lid = _all_spans(plan, ("lid_state",))
    performative = _all_spans(plan, ("blink", "performative"))
    suppression = _all_spans(plan, ("blink", "suppression"))
    semantic_spans = affect + hidden_affect + gaze + head + lid + performative + suppression
    phrases: list[ScorePhrase] = []

    for event_index, raw_event in enumerate(_as_list(plan.get("events"))):
        if not isinstance(raw_event, dict):
            continue
        bounds = _event_bounds(raw_event)
        if bounds is None:
            continue
        event_start, event_end = bounds
        boundaries = {event_start, event_end}
        for span in semantic_spans:
            start, end = _span_start(span), _span_end(span)
            if start is not None and event_start < start < event_end:
                boundaries.add(start)
            if end is not None and event_start < end < event_end:
                boundaries.add(end)
        ordered = sorted(boundaries)
        for start, end in zip(ordered, ordered[1:]):
            text = _phrase_text(raw_event, start, end)
            if not text:
                continue
            affect_ref = _active_at(affect, start)
            hidden_ref = _active_at(hidden_affect, start)
            gaze_ref = _active_at(gaze, start)
            head_ref = _active_at(head, start)
            lid_ref = _active_at(lid, start)
            blink_refs = _active(performative, start, end) + _active(suppression, start, end)
            blink_values = tuple(
                dict.fromkeys(str(span.get("value") or "").strip().upper() for span in blink_refs if span.get("value"))
            )
            state = SemanticState(
                intent=str(raw_event.get("intent") or "").strip() or None,
                lid=_lid_state(lid_ref),
                affect=_affect_state(affect_ref),
                hidden_affect=_affect_state(hidden_ref),
                gaze=_gaze_state(gaze_ref),
                head=_head_state(head_ref),
                blinks=blink_values,
            )
            phrases.append(ScorePhrase(
                number=len(phrases) + 1,
                text=text,
                event_index=event_index,
                char_start=start,
                char_end=end,
                states={alias: state},
                speaker=alias,
                references={
                    "affect": affect_ref, "hidden_affect": hidden_ref, "gaze": gaze_ref,
                    "head": head_ref, "lid": lid_ref, "blinks": blink_refs,
                },
            ))
    return phrases


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else format(value, ".12g")


def format_state(state: SemanticState) -> str:
    tags: list[str] = []
    if state.lid is not None:
        tags.append(f"<l{_format_number(state.lid)}>")
    if state.affect is not None:
        tags.append(f"<{state.affect[0]}-{state.affect[1]}>")
    if state.hidden_affect is not None:
        tags.append(f"<HEART-{state.hidden_affect[0]}-{state.hidden_affect[1]}>")
    if state.gaze is not None:
        tags.append(f"<{state.gaze[0]}-{state.gaze[1]}>")
    if state.head is not None:
        tags.append(f"<HEAD-{state.head}>")
    tags.extend(f"<{value}>" for value in state.blinks)
    return "".join(tags)


def format_single_score(phrases_or_plan: list[ScorePhrase] | dict[str, Any]) -> str:
    phrases = derive_phrases(phrases_or_plan) if isinstance(phrases_or_plan, dict) else phrases_or_plan
    blocks: list[str] = []
    for phrase in phrases:
        state_text = format_state(phrase.states["A"])
        lines = [f"{phrase.number}. {{{phrase.states['A'].intent or ''}}}"]
        if state_text:
            lines.append(f"   {state_text}")
        lines.append(f"   {phrase.text}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def format_dual_score(
    plan_a: dict[str, Any],
    plan_b: dict[str, Any],
    *,
    speakers: Iterable[str] | None = None,
) -> str:
    a_phrases = derive_phrases(plan_a, alias="A")
    b_phrases = derive_phrases(plan_b, alias="B")
    if len(a_phrases) != len(b_phrases):
        raise ValueError("Dual-character plans must derive the same number of phrases.")
    speaker_values = list(speakers) if speakers is not None else ["A"] * len(a_phrases)
    if len(speaker_values) != len(a_phrases) or any(value not in {"A", "B"} for value in speaker_values):
        raise ValueError("Dual-character speakers must provide one A/B label per phrase.")
    blocks: list[str] = []
    for index, (a_phrase, b_phrase, speaker) in enumerate(zip(a_phrases, b_phrases, speaker_values), start=1):
        text = a_phrase.text if speaker == "A" else b_phrase.text
        blocks.append(
            f"{index}. {{{a_phrase.states['A'].intent or ''}}}\n"
            f"   A:{format_state(a_phrase.states['A'])} |\n"
            f"   B:{format_state(b_phrase.states['B'])}\n"
            f"   {speaker}: {text}"
        )
    return "\n\n".join(blocks)


def _dual_state_from_plan(value: Any, *, intent: str | None) -> SemanticState:
    state = _as_dict(value)

    def affect(field: str) -> tuple[str, int] | None:
        raw = str(state.get(field) or "NONE")
        if raw == "NONE":
            return None
        match = _AFFECT.fullmatch(raw)
        return (match.group(1), int(match.group(2))) if match else None

    gaze_raw = str(state.get("gaze") or "NONE")
    gaze = None
    if gaze_raw != "NONE":
        mode, separator, target = gaze_raw.partition("-")
        if separator:
            gaze = (mode, _human_target(target))
    blink_values = []
    if state.get("blink") not in (None, "NONE"):
        blink_values.append(str(state["blink"]))
    if state.get("blink_suppression") == "SUPPRESS":
        blink_values.append("SUPPRESS")
    lid = _number(state.get("lid"))
    head = str(state.get("head") or "NONE").upper()
    return SemanticState(
        intent=intent, lid=lid, affect=affect("affect"),
        hidden_affect=affect("heart"), gaze=gaze,
        head=None if head == "NONE" else head, blinks=tuple(blink_values),
    )


def derive_dual_plan_phrases(plan: dict[str, Any]) -> list[ScorePhrase]:
    result: list[ScorePhrase] = []
    for index, raw in enumerate(_as_list(plan.get("phrases"))):
        if not isinstance(raw, dict):
            continue
        span = _as_dict(raw.get("span"))
        try:
            start, end = int(span["char_start"]), int(span["char_end"])
        except (KeyError, TypeError, ValueError):
            continue
        intent = str(raw.get("intent") or "").strip() or None
        states = _as_dict(raw.get("states"))
        result.append(ScorePhrase(
            number=len(result) + 1, text=str(span.get("text") or "").strip(),
            event_index=index, char_start=start, char_end=end,
            states={
                alias: _dual_state_from_plan(states.get(alias), intent=intent)
                for alias in ("A", "B")
            },
            speaker=str(raw.get("speaker") or "A"),
        ))
    return result


def format_dual_plan_score(plan_or_phrases: dict[str, Any] | list[ScorePhrase]) -> str:
    phrases = derive_dual_plan_phrases(plan_or_phrases) if isinstance(plan_or_phrases, dict) else plan_or_phrases
    blocks: list[str] = []
    for phrase in phrases:
        intent = phrase.states["A"].intent or ""
        blocks.append(
            f"{phrase.number}. {{{intent}}}\n"
            f"   A:{format_state(phrase.states['A'])} | B:{format_state(phrase.states['B'])}\n"
            f"   {phrase.speaker}: {phrase.text}"
        )
    return "\n\n".join(blocks)


def known_vocabulary(
    plans: Iterable[dict[str, Any]], *, extra_targets: Iterable[str] = ()
) -> tuple[set[str], set[str], set[str]]:
    """Return closed executable vocabularies plus semantic gaze targets.

    Existing plan values deliberately do not expand either closed affect list:
    an older unsupported human edit must load and display, then validate as
    invalid rather than becoming silently accepted.
    """
    visible_affects = set(DEFAULT_VISIBLE_AFFECTS)
    heart_states = set(DEFAULT_HEART_STATES)
    targets = set(DEFAULT_GAZE_TARGETS)
    for plan in plans:
        for span in _all_spans(plan, ("gaze",)):
            state = _gaze_state(span)
            if state:
                targets.add(state[1])
    targets.update(_human_target(str(value)) for value in extra_targets if str(value).strip())
    return visible_affects, heart_states, targets


def _parse_tags(
    raw: str,
    phrase_number: int,
    known_visible_affects: set[str],
    known_heart_states: set[str],
    known_targets: set[str],
    *,
    intent: str | None,
    alias: str | None = None,
) -> tuple[SemanticState, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    tags = _TAG.findall(raw)
    leftover = _TAG.sub("", raw).strip()
    if leftover or raw.count("<") != len(tags) or raw.count(">") != len(tags):
        issues.append(ValidationIssue(phrase_number, "Malformed or unclosed semantic tag."))
    lid: float | None = None
    affect: tuple[str, int] | None = None
    hidden_affect: tuple[str, int] | None = None
    gaze: tuple[str, str] | None = None
    head: str | None = None
    blinks: list[str] = []
    for value in tags:
        token = value.strip()
        lid_match = _LID.match(token)
        heart_match = _HEART.match(token)
        head_match = _HEAD.match(token)
        affect_match = _AFFECT.match(token)
        mode, separator, target = token.partition("-")
        if lid_match:
            if lid is not None:
                issues.append(ValidationIssue(phrase_number, "Duplicate lid state."))
            lid = float(lid_match.group(1))
        elif token in BLINK_VALUES:
            if token in blinks:
                issues.append(ValidationIssue(phrase_number, f"Duplicate behavior <{token}>"))
            else:
                blinks.append(token)
        elif heart_match:
            name, strength = heart_match.group(1), int(heart_match.group(2))
            subject = f"{alias} " if alias else ""
            if name not in known_heart_states:
                issues.append(ValidationIssue(phrase_number, f'Unknown {subject}heart state "{name}"'))
            elif not 0 <= strength <= 100:
                issues.append(ValidationIssue(phrase_number, f"Heart intensity must be between 0 and 100: <{token}>"))
            if hidden_affect is not None:
                issues.append(ValidationIssue(phrase_number, "Duplicate hidden affect state."))
            hidden_affect = (name, strength)
        elif head_match:
            level = head_match.group(1)
            if level not in HEAD_INVOLVEMENT:
                issues.append(ValidationIssue(phrase_number, f"Unknown behavior <{token}>"))
            if head is not None:
                issues.append(ValidationIssue(phrase_number, "Duplicate head involvement."))
            head = level
        elif separator and mode in GAZE_MODES:
            if gaze is not None:
                issues.append(ValidationIssue(phrase_number, "Duplicate gaze behavior."))
            human_target = _human_target(target)
            if human_target not in known_targets:
                issues.append(ValidationIssue(phrase_number, f"Unknown behavior <{token}>"))
            gaze = (mode, human_target)
        elif affect_match:
            name, strength = affect_match.group(1), int(affect_match.group(2))
            subject = f"{alias} " if alias else ""
            if name not in known_visible_affects:
                issues.append(ValidationIssue(phrase_number, f'Unknown {subject}visible affect "{name}"'))
            elif not 0 <= strength <= 100:
                issues.append(ValidationIssue(phrase_number, f"Visible affect intensity must be between 0 and 100: <{token}>"))
            if affect is not None:
                issues.append(ValidationIssue(phrase_number, "Duplicate affect state."))
            affect = (name, strength)
        else:
            issues.append(ValidationIssue(phrase_number, f"Unknown behavior <{token}>"))
    return SemanticState(
        intent=intent,
        lid=lid,
        affect=affect,
        hidden_affect=hidden_affect,
        gaze=gaze,
        head=head,
        blinks=tuple(blinks),
    ), issues


def parse_score(
    text: str,
    *,
    mode: str = "single",
    known_visible_affects: Iterable[str] = DEFAULT_VISIBLE_AFFECTS,
    known_heart_states: Iterable[str] = DEFAULT_HEART_STATES,
    known_targets: Iterable[str] = DEFAULT_GAZE_TARGETS,
) -> ParseResult:
    if mode not in {"single", "dual"}:
        raise ValueError("mode must be 'single' or 'dual'")
    visible_affect_names = set(known_visible_affects)
    heart_names = set(known_heart_states)
    gaze_targets = {_human_target(value) for value in known_targets}
    lines = text.splitlines()
    phrases: list[ParsedPhrase] = []
    issues: list[ValidationIssue] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        match = _HEADER.match(lines[index])
        if not match:
            issues.append(ValidationIssue(None, f"Expected a numbered phrase near line {index + 1}."))
            index += 1
            continue
        number, header = int(match.group(1)), match.group(2)
        intent_match = _INTENT.match(header)
        intent = intent_match.group(1) if intent_match else None
        if not intent_match:
            issues.append(ValidationIssue(number, "Intent heading must use {INTENT_NAME}."))
        index += 1
        body_lines: list[str] = []
        while index < len(lines) and not _HEADER.match(lines[index]):
            if lines[index].strip():
                body_lines.append(lines[index].strip())
            index += 1
        states: dict[str, SemanticState] = {}
        speaker = "A"
        if mode == "single":
            has_state_line = bool(body_lines and body_lines[0].startswith("<"))
            state_line = body_lines[0] if has_state_line else ""
            dialogue_lines = body_lines[1:] if has_state_line else body_lines
            dialogue = " ".join(dialogue_lines)
            if not dialogue:
                issues.append(ValidationIssue(number, "Dialogue text is required."))
            state, tag_issues = _parse_tags(
                state_line, number, visible_affect_names, heart_names, gaze_targets, intent=intent
            )
            states["A"] = state
            issues.extend(tag_issues)
        else:
            speaker_index = next(
                (
                    line_index for line_index in range(len(body_lines) - 1, -1, -1)
                    if re.match(r"^[AB]:\s+[^<].*$", body_lines[line_index])
                ),
                -1,
            )
            state_text = " ".join(body_lines[:speaker_index]) if speaker_index >= 0 else " ".join(body_lines)
            dialogue_line = body_lines[speaker_index] if speaker_index >= 0 else ""
            dual_match = re.match(r"^A:\s*(.*?)\s*\|\s*B:\s*(.*?)\s*$", state_text)
            if not dual_match:
                issues.append(ValidationIssue(number, "Dual state must use A:<...> | B:<...>."))
                states = {"A": SemanticState(intent=intent), "B": SemanticState(intent=intent)}
            else:
                for alias, raw_tags in zip(("A", "B"), dual_match.groups()):
                    states[alias], tag_issues = _parse_tags(
                        raw_tags, number, visible_affect_names, heart_names, gaze_targets,
                        intent=intent, alias=alias,
                    )
                    issues.extend(tag_issues)
            speaker_match = re.match(r"^([AB]):\s*(.*)$", dialogue_line)
            if not speaker_match:
                issues.append(ValidationIssue(number, "Dialogue must begin with speaker A: or B:."))
                dialogue = ""
            else:
                speaker, dialogue = speaker_match.group(1), speaker_match.group(2).strip()
                if not dialogue:
                    issues.append(ValidationIssue(number, "Dialogue text is required."))
        phrases.append(ParsedPhrase(number, dialogue, states, speaker))

    expected = list(range(1, len(phrases) + 1))
    actual = [phrase.number for phrase in phrases]
    if actual != expected:
        issues.append(ValidationIssue(None, "Phrase numbers must be unique, contiguous, and ordered from 1."))
    return ParseResult(tuple(phrases), tuple(issues))


def _set_affect(span: dict[str, Any], state: tuple[str, int]) -> None:
    span["state"], strength = state
    span["intensity"] = strength / 100.0
    span["value"] = f"{state[0]}-{strength}"


def _set_gaze(span: dict[str, Any], state: tuple[str, str]) -> None:
    span["mode"], span["target"] = state
    span["value"] = f"{state[0]}-{state[1]}"


def _set_lid(span: dict[str, Any], value: float) -> None:
    numeric: int | float = int(value) if float(value).is_integer() else value
    span["lid_state"] = numeric
    span["value"] = str(numeric)


def _set_head(span: dict[str, Any], level: str) -> None:
    span["value"] = level
    span["involvement"] = HEAD_INVOLVEMENT[level]


def _human_span(phrase: ScorePhrase, value: str) -> dict[str, Any]:
    return {
        "source_tag": f"human_phrase_{phrase.number}",
        "char_start": phrase.char_start,
        "char_end": phrase.char_end,
        "value": value,
        "author": "human",
    }


class DualPerformanceScoreModel:
    """Editable score backed by one canonical dual_performance_plan_v0."""

    def __init__(self, plan: dict[str, Any], *, extra_targets: Iterable[str] = ()) -> None:
        if plan.get("schema_version") != "dual_performance_plan_v0":
            raise ValueError("DualPerformanceScoreModel requires dual_performance_plan_v0.")
        self.plan = deepcopy(plan)
        self.phrases = derive_dual_plan_phrases(self.plan)
        self.visible_affects = set(DEFAULT_VISIBLE_AFFECTS)
        self.heart_states = set(DEFAULT_HEART_STATES)
        self.targets = set(DEFAULT_GAZE_TARGETS)
        for phrase in self.phrases:
            for state in phrase.states.values():
                if state.gaze:
                    self.targets.add(state.gaze[1])
        self.targets.update(_human_target(str(value)) for value in extra_targets if str(value).strip())
        snapshot = _as_dict(self.plan.get("authoring")).get("original_semantic_proposal")
        if isinstance(snapshot, list) and len(snapshot) == len(self.phrases):
            self.original = deepcopy(snapshot)
        else:
            self.original = [
                {
                    "intent": phrase.states["A"].intent,
                    "states": {
                        alias: deepcopy(self.plan["phrases"][index]["states"][alias])
                        for alias in ("A", "B")
                    },
                }
                for index, phrase in enumerate(self.phrases)
            ]

    @property
    def score_text(self) -> str:
        return format_dual_plan_score(self.phrases)

    def validate(self, text: str) -> ParseResult:
        result = parse_score(
            text, mode="dual", known_visible_affects=self.visible_affects,
            known_heart_states=self.heart_states, known_targets=self.targets,
        )
        errors = list(result.errors)
        if len(result.phrases) != len(self.phrases):
            errors.append(ValidationIssue(None, f"Expected {len(self.phrases)} phrases, found {len(result.phrases)}."))
        for parsed, canonical in zip(result.phrases, self.phrases):
            if parsed.text != canonical.text:
                errors.append(ValidationIssue(parsed.number, "Dialogue text must match the canonical transcript."))
            if parsed.speaker != canonical.speaker:
                errors.append(ValidationIssue(parsed.number, "Dialogue speaker must match the canonical transcript."))
        return ParseResult(result.phrases, tuple(errors))

    @staticmethod
    def _canonical_state(
        state: SemanticState, original_state: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        performative = next((value for value in state.blinks if value != "SUPPRESS"), "NONE")
        lid: int | float | None = state.lid
        if lid is not None and float(lid).is_integer():
            lid = int(lid)
        gaze = f"{state.gaze[0]}-{state.gaze[1]}" if state.gaze else "NONE"
        original_gaze = str(_as_dict(original_state).get("gaze") or "NONE")
        if state.gaze and original_gaze != "NONE":
            original_mode, separator, original_target = original_gaze.partition("-")
            if (
                separator and original_mode == state.gaze[0]
                and _human_target(original_target) == state.gaze[1]
            ):
                gaze = original_gaze
        return {
            "affect": f"{state.affect[0]}-{state.affect[1]}" if state.affect else "NONE",
            "heart": f"{state.hidden_affect[0]}-{state.hidden_affect[1]}" if state.hidden_affect else "NONE",
            "gaze": gaze,
            "head": state.head or "NONE", "lid": "NONE" if lid is None else lid,
            "blink_suppression": "SUPPRESS" if "SUPPRESS" in state.blinks else "NONE",
        }

    def apply(self, text: str) -> dict[str, Any]:
        result = self.validate(text)
        if not result.valid:
            raise ValueError("\n".join(str(error) for error in result.errors))
        records: list[dict[str, Any]] = []
        for index, parsed in enumerate(result.phrases):
            phrase = self.plan["phrases"][index]
            phrase["intent"] = parsed.states["A"].intent
            existing_states = deepcopy(_as_dict(phrase.get("states")))
            phrase["states"] = {
                alias: self._canonical_state(
                    parsed.states[alias], _as_dict(existing_states.get(alias))
                )
                for alias in ("A", "B")
            }
            changed: list[str] = []
            original = self.original[index]
            if phrase["intent"] != original["intent"]:
                changed.append("intent")
            for alias in ("A", "B"):
                for field, value in phrase["states"][alias].items():
                    if value != original["states"][alias].get(field):
                        changed.append(f"{alias}.{field}")
            if changed:
                records.append({"phrase_number": index + 1, "phrase_id": phrase.get("phrase_id"), "changed_categories": changed})
        authoring = self.plan.setdefault("authoring", {})
        authoring["semantic_score_version"] = "dual_semantic_score_v1"
        authoring.setdefault("original_semantic_proposal", deepcopy(self.original))
        authoring["manually_edited_phrases"] = records
        self.phrases = derive_dual_plan_phrases(self.plan)
        return self.plan

    def is_manually_edited(self, phrase_number: int) -> bool:
        records = _as_dict(self.plan.get("authoring")).get("manually_edited_phrases", [])
        return any(isinstance(row, dict) and row.get("phrase_number") == phrase_number for row in records)

    def rationale_view(self, phrase_number: int) -> str:
        if not 1 <= phrase_number <= len(self.phrases):
            return "No such phrase."
        score_phrase = self.phrases[phrase_number - 1]
        phrase = self.plan["phrases"][phrase_number - 1]
        rationale = _as_dict(phrase.get("rationale"))
        lines = [f"Phrase {phrase_number}", "", f'"{score_phrase.text}"', ""]
        if self.is_manually_edited(phrase_number):
            lines.extend(["Phrase manually edited. AI rationale corresponds to the original proposal.", ""])
        lines.extend(["Intent", f"Reason: {rationale.get('intent') or ''}", ""])
        characters = _as_dict(self.plan.get("characters"))
        states = _as_dict(phrase.get("states"))
        for alias in ("A", "B"):
            lines.append(f"{alias} — {characters.get(alias, alias)}")
            state = _as_dict(states.get(alias))
            reasons = _as_dict(rationale.get(alias))
            for field in ("affect", "heart", "gaze", "head", "lid", "blink", "blink_suppression"):
                value = state.get(field, "NONE")
                if value in (None, "NONE") and not reasons.get(field):
                    continue
                lines.extend([f"{field.replace('_', ' ').title()}: {value}", f"Reason: {reasons.get(field) or ''}", ""])
        return "\n".join(lines).rstrip()


class PerformanceScoreModel:
    """Editable single-character score backed by a canonical plan copy."""

    def __init__(self, plan: dict[str, Any], *, extra_targets: Iterable[str] = ()) -> None:
        self.plan = deepcopy(plan)
        self.phrases = derive_phrases(self.plan)
        self.original_states = self._original_proposal_states()
        self.visible_affects, self.heart_states, self.targets = known_vocabulary(
            [self.plan], extra_targets=extra_targets
        )

    def _original_proposal_states(self) -> list[SemanticState]:
        snapshot = _as_dict(_as_dict(self.plan.get("authoring")).get("original_semantic_proposal"))
        snapshot_events = _as_list(snapshot.get("events"))
        if not snapshot_events:
            return [deepcopy(phrase.states["A"]) for phrase in self.phrases]
        original_plan = deepcopy(self.plan)
        by_event_id = {
            str(event.get("event_id")): event for event in snapshot_events if isinstance(event, dict)
        }
        for event in original_plan.get("events", []):
            if not isinstance(event, dict):
                continue
            saved = by_event_id.get(str(event.get("event_id")))
            if not saved:
                continue
            for key in ("intent", "affect", "gaze", "head", "lid_state", "blink"):
                if key in saved:
                    event[key] = deepcopy(saved[key])
        original_phrases = derive_phrases(original_plan)
        states: list[SemanticState] = []
        for phrase in self.phrases:
            source = next(
                (
                    candidate for candidate in original_phrases
                    if candidate.event_index == phrase.event_index
                    and candidate.char_start <= phrase.char_start < candidate.char_end
                ),
                None,
            )
            states.append(deepcopy((source or phrase).states["A"]))
        return states

    @property
    def score_text(self) -> str:
        return format_single_score(self.phrases)

    def validate(self, text: str) -> ParseResult:
        result = parse_score(
            text, known_visible_affects=self.visible_affects,
            known_heart_states=self.heart_states, known_targets=self.targets,
        )
        errors = list(result.errors)
        if len(result.phrases) != len(self.phrases):
            errors.append(ValidationIssue(None, f"Expected {len(self.phrases)} phrases, found {len(result.phrases)}."))
        intents_by_event: dict[int, str | None] = {}
        for parsed, source in zip(result.phrases, self.phrases):
            if parsed.text != source.text:
                errors.append(ValidationIssue(parsed.number, "Dialogue text must match the canonical transcript."))
            intent = parsed.states["A"].intent
            previous = intents_by_event.setdefault(source.event_index, intent)
            if previous != intent:
                errors.append(ValidationIssue(
                    parsed.number,
                    "All phrases in the same intent event must use the same intent heading.",
                ))
        return ParseResult(result.phrases, tuple(errors))

    def apply(self, text: str) -> dict[str, Any]:
        result = self.validate(text)
        if not result.valid:
            raise ValueError("\n".join(str(error) for error in result.errors))
        edited_records: list[dict[str, Any]] = []
        requires_rebuild = False
        for parsed, phrase, original in zip(result.phrases, self.phrases, self.original_states):
            new = parsed.states["A"]
            event = self.plan["events"][phrase.event_index]
            requires_rebuild = requires_rebuild or new != phrase.states["A"]
            if new != original:
                changed_from_original = [
                    name for name in (
                        "intent", "affect", "hidden_affect", "gaze", "head", "lid", "blinks"
                    )
                    if getattr(new, name) != getattr(original, name)
                ]
                edited_records.append({
                    "phrase_number": phrase.number,
                    "event_id": event.get("event_id"),
                    "char_start": phrase.char_start,
                    "char_end": phrase.char_end,
                    "changed_categories": changed_from_original,
                })
        authoring = self.plan.setdefault("authoring", {})
        authoring["semantic_score_version"] = "semantic_score_v1"
        authoring["manually_edited_phrases"] = edited_records
        if requires_rebuild:
            authoring.setdefault("original_semantic_proposal", {
                "events": [
                    {
                        "event_id": event.get("event_id"),
                        "intent": event.get("intent"),
                        "affect": deepcopy(event.get("affect", {})),
                        "gaze": deepcopy(event.get("gaze", [])),
                        "head": deepcopy(event.get("head", [])),
                        "lid_state": deepcopy(event.get("lid_state", [])),
                        "blink": deepcopy(event.get("blink", {})),
                    }
                    for event in self.plan.get("events", []) if isinstance(event, dict)
                ]
            })
            self._rebuild_edited_semantics(result.phrases)
            self.phrases = derive_phrases(self.plan)
        return self.plan

    def _rebuild_edited_semantics(self, parsed_phrases: tuple[ParsedPhrase, ...]) -> None:
        """Materialize phrase-local edits as canonical spans, retaining originals in authoring."""
        parsed_by_number = {phrase.number: phrase for phrase in parsed_phrases}

        def groups(event_phrases: list[ScorePhrase], attribute: str) -> list[tuple[int, int, Any, dict[str, Any] | None, bool]]:
            values: list[tuple[ScorePhrase, Any]] = [
                (phrase, getattr(parsed_by_number[phrase.number].states["A"], attribute))
                for phrase in event_phrases
            ]
            result_groups: list[tuple[int, int, Any, dict[str, Any] | None, bool]] = []
            for phrase, value in values:
                if value is None:
                    continue
                reference_name = "lid" if attribute == "lid" else attribute
                reference = phrase.references.get(reference_name)
                template = reference if isinstance(reference, dict) else None
                changed = value != getattr(phrase.states["A"], attribute)
                if result_groups and result_groups[-1][1] == phrase.char_start and result_groups[-1][2] == value:
                    old = result_groups[-1]
                    result_groups[-1] = (old[0], phrase.char_end, value, old[3] or template, old[4] or changed)
                else:
                    result_groups.append((phrase.char_start, phrase.char_end, value, template, changed))
            return result_groups

        def materialize(
            event_phrases: list[ScorePhrase], attribute: str, setter: Any
        ) -> list[dict[str, Any]]:
            spans: list[dict[str, Any]] = []
            for start, end, value, template, changed in groups(event_phrases, attribute):
                span = deepcopy(template) if template is not None else _human_span(event_phrases[0], "")
                span["char_start"], span["char_end"] = start, end
                if changed:
                    original_tag = str(span.get("source_tag") or "")
                    span["original_source_tag"] = original_tag or None
                    span["source_tag"] = f"human_phrase_{next(p.number for p in event_phrases if p.char_start == start)}_{attribute}"
                    span["author"] = "human"
                setter(span, value)
                spans.append(span)
            return spans

        for event_index, event in enumerate(self.plan.get("events", [])):
            if not isinstance(event, dict):
                continue
            event_phrases = [phrase for phrase in self.phrases if phrase.event_index == event_index]
            if not event_phrases:
                continue
            event["intent"] = parsed_by_number[event_phrases[0].number].states["A"].intent
            event.setdefault("affect", {})["visible"] = materialize(event_phrases, "affect", _set_affect)
            event["affect"]["hidden"] = materialize(
                event_phrases, "hidden_affect", _set_affect
            )
            event["gaze"] = materialize(event_phrases, "gaze", _set_gaze)
            event["head"] = materialize(event_phrases, "head", _set_head)
            event["lid_state"] = materialize(event_phrases, "lid", _set_lid)
            blink_output = {"performative": [], "suppression": []}
            for blink_value in BLINK_VALUES:
                active = [
                    phrase for phrase in event_phrases
                    if blink_value in parsed_by_number[phrase.number].states["A"].blinks
                ]
                run: list[ScorePhrase] = []
                for phrase in active + [None]:
                    if phrase is not None and (not run or run[-1].char_end == phrase.char_start):
                        run.append(phrase)
                        continue
                    if run:
                        original_refs = run[0].references.get("blinks")
                        template = next(
                            (ref for ref in original_refs if isinstance(ref, dict) and str(ref.get("value", "")).upper() == blink_value),
                            None,
                        ) if isinstance(original_refs, list) else None
                        span = deepcopy(template) if template else _human_span(run[0], blink_value)
                        span["char_start"], span["char_end"], span["value"] = run[0].char_start, run[-1].char_end, blink_value
                        original_values = {value for item in run for value in item.states["A"].blinks}
                        if blink_value not in original_values or template is None:
                            span["original_source_tag"] = span.get("source_tag")
                            span["source_tag"] = f"human_phrase_{run[0].number}_blink"
                            span["author"] = "human"
                        kind = "suppression" if blink_value == "SUPPRESS" else "performative"
                        blink_output[kind].append(span)
                    run = [phrase] if phrase is not None else []
            event["blink"] = blink_output

    def is_manually_edited(self, phrase_number: int) -> bool:
        records = _as_dict(self.plan.get("authoring")).get("manually_edited_phrases", [])
        return any(item.get("phrase_number") == phrase_number for item in records if isinstance(item, dict))

    def rationale_for_phrase(self, phrase_number: int) -> list[RationaleItem]:
        if not 1 <= phrase_number <= len(self.phrases):
            return []
        event = self.plan["events"][self.phrases[phrase_number - 1].event_index]
        phrase = self.phrases[phrase_number - 1]
        rationale = _as_dict(event.get("rationale"))
        items: list[RationaleItem] = []
        relevant_tags = {str(event.get("source_intent_tag") or "")}
        for reference in phrase.references.values():
            rows = reference if isinstance(reference, list) else [reference]
            for row in rows:
                if isinstance(row, dict):
                    relevant_tags.add(str(row.get("original_source_tag") or row.get("source_tag") or ""))

        def add(category: str, value: Any) -> None:
            rows = value if isinstance(value, list) else [value]
            for row in rows:
                if isinstance(row, dict) and row.get("reason") and str(row.get("source_tag") or "") in relevant_tags:
                    items.append(RationaleItem(category, str(row.get("source_tag") or category), str(row["reason"])))

        add("Intent", rationale.get("intent"))
        affect = _as_dict(rationale.get("affect"))
        add("Visible Affect", affect.get("visible"))
        add("Hidden Affect", affect.get("hidden"))
        add("Gaze", rationale.get("gaze"))
        add("Head", rationale.get("head"))
        add("Lid", rationale.get("lid_state"))
        blink = _as_dict(rationale.get("blink"))
        add("Performative Blink", blink.get("performative"))
        add("Blink Suppression", blink.get("suppression"))
        seen: set[tuple[str, str, str]] = set()
        return [item for item in items if not ((item.category, item.behavior, item.reason) in seen or seen.add((item.category, item.behavior, item.reason)))]


def format_rationale_view(
    model: PerformanceScoreModel | DualPerformanceScoreModel, phrase_number: int
) -> str:
    if isinstance(model, DualPerformanceScoreModel):
        return model.rationale_view(phrase_number)
    if not 1 <= phrase_number <= len(model.phrases):
        return "No such phrase."
    phrase = model.phrases[phrase_number - 1]
    lines = [f"Phrase {phrase_number}", "", f'"{phrase.text}"', ""]
    if model.is_manually_edited(phrase_number):
        lines.extend(["Phrase manually edited. AI rationale corresponds to the original proposal.", ""])
    items = model.rationale_for_phrase(phrase_number)
    if not items:
        lines.append("No original AI rationale is associated with this phrase.")
    for item in items:
        lines.extend([f"{item.category} ({item.behavior})", f"Reason: {item.reason}", ""])
    return "\n".join(lines).rstrip()
