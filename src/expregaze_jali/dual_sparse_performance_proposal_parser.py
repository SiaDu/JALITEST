"""Strict parser for sparse dual-character Performance Plan v2 proposals."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from expregaze_jali.performance_proposal_parser import ProposalValidationError, SemanticVocabulary
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel, speaker_key

PERSISTENT_CHANNELS = ("affect", "gaze", "head")
BLINK_VALUES = {"SLOW_BLINK", "DOUBLE_BLINK", "EYE_CLOSE_HOLD", "EYE_OPEN"}
HEAD_VALUES = {f"HEAD-{direction}-{strength}" for direction in ("UP", "DOWN", "TILT_LEFT", "TILT_RIGHT") for strength in ("SUBTLE", "MEDIUM", "STRONG")} | {"HEAD-NONE"}
DIRECTION_TARGETS = {"RIGHT", "LEFT", "DOWN", "DOWN_LEFT", "DOWN_RIGHT", "UP", "UP_LEFT", "UP_RIGHT"}
REMOVED_CHANNELS = {"heart", "lid", "blink_suppression"}
_SECTION = re.compile(r"^\[(GAZE_TARGETS|ANALYZE|INITIAL|CHANGES)\]\s*$", re.IGNORECASE)
_EVENT_ID = re.compile(r"^E\d+$", re.IGNORECASE)
_FIELD = re.compile(r"^([a-z_]+)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_ANCHOR = re.compile(r"^w\d{4,}$", re.IGNORECASE)
_AFFECT = re.compile(r"^(.+)-(\d+)$")
_GAZE = re.compile(r"^(GAZE|GLANCE)-(.+)$")


def _normalize_affect(value: str, *, event_id: str, vocabulary: SemanticVocabulary) -> str:
    text = value.strip()
    if text.upper() == "MASK-NONE":
        return "MASK-NONE"
    match = _AFFECT.fullmatch(text)
    if not match:
        raise ProposalValidationError(f'{event_id}: Invalid affect value "{value}"')
    state, intensity_text = match.groups()
    canonical = next((v for v in vocabulary.affect_states.values() if v.lower() == state.lower()), None)
    if canonical is None:
        raise ProposalValidationError(f'{event_id}: Unknown Mask state "{state}"')
    intensity = int(intensity_text)
    if intensity <= 0:
        raise ProposalValidationError(f"{event_id}: Affect intensity must be a positive integer")
    return f"{canonical}-{intensity}"


def _normalize_gaze(value: str, *, event_id: str, characters: tuple[str, str]) -> str:
    text = value.strip()
    if text.upper() in {"GAZE-NONE", "GLANCE-NONE"}:
        raise ProposalValidationError(f'{event_id}: {text.upper()} is an internal runtime reset, not an authored gaze value')
    match = _GAZE.fullmatch(text)
    if not match:
        raise ProposalValidationError(f'{event_id}: Invalid gaze value "{value}"')
    mode, target = match.group(1).upper(), match.group(2)
    if target.upper() == "NONE":
        raise ProposalValidationError(f'{event_id}: {mode}-NONE is an internal runtime reset, not an authored gaze value')
    if target.upper() in DIRECTION_TARGETS:
        return f"{mode}-{target.upper()}"
    actor = next((name for name in characters if speaker_key(name) == speaker_key(target)), None)
    if actor is not None:
        return f"{mode}-{actor}"
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_'-]*", target):
        raise ProposalValidationError(f'{event_id}: Invalid gaze target "{target}"')
    return f"{mode}-{target}"


def parse_dual_sparse_performance_proposal(source: str | Path, *, vocabulary: SemanticVocabulary, anchor_model: ConversationAnchorModel) -> dict[str, Any]:
    """Parse v2 sparse changes without repairing invalid semantic values."""
    text = Path(source).read_text(encoding="utf-8") if isinstance(source, Path) else str(source)
    sections: dict[str, list[str]] = {"GAZE_TARGETS": [], "ANALYZE": [], "INITIAL": [], "CHANGES": []}
    current: str | None = None
    seen: list[str] = []
    for line in text.splitlines():
        section = _SECTION.fullmatch(line.strip())
        if section:
            current = section.group(1).upper()
            if current in seen:
                raise ProposalValidationError(f"Duplicate [{current}] section")
            seen.append(current)
        elif current is not None:
            sections[current].append(line)
        elif line.strip():
            raise ProposalValidationError("Text before the first proposal section is not allowed")
    if seen not in (["GAZE_TARGETS", "INITIAL", "CHANGES"], ["ANALYZE", "INITIAL", "CHANGES"]):
        raise ProposalValidationError("Sections must be [GAZE_TARGETS], [INITIAL], [CHANGES] (or legacy [ANALYZE]).")
    characters = tuple(anchor_model.aliases.values())
    raw_candidates = [line.strip().upper() for line in sections["GAZE_TARGETS"] if line.strip()]
    if "GAZE_TARGETS" in seen and not raw_candidates:
        raise ProposalValidationError("[GAZE_TARGETS] requires NONE or at least one candidate.")
    if "NONE" in raw_candidates:
        if raw_candidates != ["NONE"]:
            raise ProposalValidationError("[GAZE_TARGETS] NONE must appear alone.")
        candidates = []
    else:
        candidates = raw_candidates
    if len(candidates) > 5 or len(set(candidates)) != len(candidates):
        raise ProposalValidationError("[GAZE_TARGETS] requires at most five unique candidates.")
    if any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", item) or item in DIRECTION_TARGETS or item in {name.upper() for name in characters} for item in candidates):
        raise ProposalValidationError("[GAZE_TARGETS] contains an invalid, directional, or character target.")
    raw_initial: dict[str, dict[str, str]] = {}
    current_actor: str | None = None
    for line in sections["INITIAL"]:
        stripped = line.strip()
        if not stripped:
            continue
        actor = next((name for name in characters if speaker_key(name) == speaker_key(stripped)), None)
        if actor is not None:
            if actor in raw_initial:
                raise ProposalValidationError(f"Duplicate [INITIAL] actor {actor}")
            raw_initial[actor] = {}
            current_actor = actor
            continue
        match = _FIELD.fullmatch(stripped)
        if current_actor is None or match is None:
            raise ProposalValidationError(f"Malformed [INITIAL] line: {stripped}")
        field, value = match.group(1).lower(), match.group(2)
        if field in REMOVED_CHANNELS or field == "blink":
            raise ProposalValidationError(f"{current_actor}: v2 initial channel {field} is not allowed")
        if field not in {*PERSISTENT_CHANNELS, "reason"}:
            raise ProposalValidationError(f"{current_actor}: Unknown initial field {field}")
        if field in raw_initial[current_actor]:
            raise ProposalValidationError(f"{current_actor}: Duplicate initial field {field}")
        raw_initial[current_actor][field] = value
    missing_initial = [actor for actor in characters if actor not in raw_initial]
    if missing_initial:
        raise ProposalValidationError("[INITIAL] requires one explicit actor block for: " + ", ".join(missing_initial))
    initial_states: dict[str, dict[str, str]] = {}
    initial_reasons: dict[str, str | None] = {}
    for actor in characters:
        raw = raw_initial[actor]
        if not raw.get("affect", "").strip():
            raise ProposalValidationError(f"{actor} initial: affect is required")
        affect = _normalize_affect(raw["affect"], event_id=f"{actor} initial", vocabulary=vocabulary)
        if affect == "MASK-NONE":
            raise ProposalValidationError(f"{actor} initial: affect must be a visible Mask, not MASK-NONE")
        if not raw.get("gaze", "").strip():
            raise ProposalValidationError(f"{actor} initial: gaze is required")
        gaze = _normalize_gaze(raw["gaze"], event_id=f"{actor} initial", characters=characters)
        if gaze.startswith("GLANCE-"):
            raise ProposalValidationError(f"{actor} initial: GLANCE is instantaneous; initial gaze must be persistent GAZE")
        head = raw.get("head", "HEAD-NONE").strip().upper()
        if head not in HEAD_VALUES:
            raise ProposalValidationError(f'{actor} initial: Invalid v2 head value "{raw.get("head")}"')
        initial_states[actor] = {"affect": affect, "gaze": gaze, "head": head}
        reason = raw.get("reason", "").strip()
        if not reason:
            raise ProposalValidationError(f"{actor} initial: reason is required")
        initial_reasons[actor] = reason
    raw_events: list[dict[str, str]] = []
    event: dict[str, str] | None = None
    for line in sections["CHANGES"]:
        stripped = line.strip()
        if not stripped:
            continue
        if _EVENT_ID.fullmatch(stripped):
            event = {"event_id": stripped.upper()}
            raw_events.append(event)
            continue
        match = _FIELD.fullmatch(stripped)
        if event is None or match is None:
            raise ProposalValidationError(f"Malformed [CHANGES] line: {stripped}")
        field, value = match.group(1).lower(), match.group(2)
        allowed = {"actor", "anchor", "affect", "gaze", "head", "blink", "reason"}
        if field in REMOVED_CHANNELS:
            raise ProposalValidationError(f"{event['event_id']}: v2 channel {field} is not allowed")
        if field not in allowed:
            raise ProposalValidationError(f"{event['event_id']}: Unknown field {field}")
        if field in event:
            raise ProposalValidationError(f"{event['event_id']}: Duplicate field {field}")
        event[field] = value
    ids = [row["event_id"] for row in raw_events]
    if len(ids) != len(set(ids)):
        raise ProposalValidationError("Event IDs must be unique")
    anchor_ids = {anchor.anchor_id for anchor in anchor_model.anchors}
    events: list[dict[str, Any]] = []
    for raw in raw_events:
        event_id = raw["event_id"]
        missing = [field for field in ("actor", "anchor", "reason") if not raw.get(field, "").strip()]
        if missing:
            raise ProposalValidationError(f"{event_id}: Missing required fields: {', '.join(missing)}")
        actor = next((name for name in characters if speaker_key(name) == speaker_key(raw["actor"])), None)
        if actor is None:
            raise ProposalValidationError(f'{event_id}: Unknown actor "{raw["actor"]}"')
        anchor_id = raw["anchor"].strip().lower()
        if not _ANCHOR.fullmatch(anchor_id) or anchor_id not in anchor_ids:
            raise ProposalValidationError(f'{event_id}: Unknown anchor "{raw["anchor"]}"')
        changes: dict[str, str] = {}
        if "affect" in raw:
            changes["affect"] = _normalize_affect(raw["affect"], event_id=event_id, vocabulary=vocabulary)
        if "gaze" in raw:
            changes["gaze"] = _normalize_gaze(raw["gaze"], event_id=event_id, characters=characters)
        if "head" in raw:
            head = raw["head"].strip().upper()
            if head not in HEAD_VALUES:
                raise ProposalValidationError(f'{event_id}: Invalid v2 head value "{raw["head"]}"')
            changes["head"] = head
        if "blink" in raw:
            blink = raw["blink"].strip().upper()
            if blink not in BLINK_VALUES:
                raise ProposalValidationError(f'{event_id}: Invalid performative blink "{raw["blink"]}"')
            changes["blink"] = blink
        if not changes:
            raise ProposalValidationError(f"{event_id}: At least one semantic change is required")
        events.append({"event_id": event_id, "actor": actor, "anchor_id": anchor_id, "changes": changes, "reason": raw.get("reason", "").strip() or None})
    identities = [(event["actor"], event["anchor_id"]) for event in events]
    if len(identities) != len(set(identities)):
        raise ProposalValidationError("Each actor may have at most one v2 event at the same anchor")
    return {"gaze_target_candidates": candidates, "initial_states": initial_states, "initial_reasons": initial_reasons, "events": events, "diagnostics": {"errors": [], "warnings": []}}
