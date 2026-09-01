"""Strict parser for the small dual-character Semantic Beat IR."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from expregaze_jali.dual_sparse_performance_proposal_parser import BLINK_VALUES, HEAD_VALUES
from expregaze_jali.performance_proposal_parser import ProposalValidationError, SemanticVocabulary
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel, speaker_key

_SECTION = re.compile(r"^\[(INITIAL|BEATS)\]\s*$", re.IGNORECASE)
_EVENT_ID = re.compile(r"^E\d+$", re.IGNORECASE)
_FIELD = re.compile(r"^([a-z_]+)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_ANCHOR = re.compile(r"^w\d{4,}$", re.IGNORECASE)
_AFFECT = re.compile(r"^(.+)-(\d+)$")
_TARGET = re.compile(r"^[A-Za-z][A-Za-z0-9_'\-]*$")


def is_reserved_attention_target(value: str) -> bool:
    """Return whether documentation-only TARGET was used as executable content."""
    return value.strip().casefold() == "target"


def _affect(
    value: str, *, label: str, vocabulary: SemanticVocabulary, allow_none: bool = False,
) -> str:
    if value.strip().upper() == "MASK-NONE":
        if allow_none:
            return "MASK-NONE"
        raise ProposalValidationError(f"{label}: MASK-NONE is not allowed")
    match = _AFFECT.fullmatch(value.strip())
    if not match:
        raise ProposalValidationError(f'{label}: Invalid affect value "{value}"')
    state, intensity = match.groups()
    canonical = next((item for item in vocabulary.affect_states.values() if item.lower() == state.lower()), None)
    if canonical is None:
        raise ProposalValidationError(f'{label}: Unknown Mask state "{state}"')
    if int(intensity) <= 0:
        raise ProposalValidationError(f"{label}: Affect intensity must be a positive integer")
    return f"{canonical}-{intensity}"


def _focus(value: str, *, label: str, characters: tuple[str, str]) -> str:
    target = value.strip()
    if not _TARGET.fullmatch(target):
        raise ProposalValidationError(f'{label}: Invalid focus target "{target}"')
    if is_reserved_attention_target(target):
        raise ProposalValidationError(f'{label}: "TARGET" is a reserved placeholder and cannot be used as a focus target')
    actor = next((name for name in characters if speaker_key(name) == speaker_key(target)), None)
    return actor or target.upper()


def _eye_action(value: str, *, label: str, characters: tuple[str, str]) -> dict[str, str]:
    parts = value.strip().split()
    if not parts or parts[0] != "brief_check":
        raise ProposalValidationError(f'{label}: eye_action must be "brief_check TARGET"')
    if len(parts) != 2:
        raise ProposalValidationError(f'{label}: Invalid eye_action target "{" ".join(parts[1:])}"')
    target = parts[1]
    if not _TARGET.fullmatch(target):
        raise ProposalValidationError(f'{label}: Invalid eye_action target "{target}"')
    if is_reserved_attention_target(target):
        raise ProposalValidationError(f'{label}: "TARGET" is a reserved placeholder and cannot be used as an eye-action target')
    actor = next((name for name in characters if speaker_key(name) == speaker_key(target)), None)
    return {"action": "brief_check", "target": actor or target.upper()}


def parse_dual_semantic_beats(
    source: str | Path, *, vocabulary: SemanticVocabulary, anchor_model: ConversationAnchorModel,
) -> dict[str, Any]:
    text = Path(source).read_text(encoding="utf-8") if isinstance(source, Path) else str(source)
    sections = {"INITIAL": [], "BEATS": []}; current: str | None = None; seen: list[str] = []
    for line in text.splitlines():
        section = _SECTION.fullmatch(line.strip())
        if section:
            current = section.group(1).upper()
            if current in seen: raise ProposalValidationError(f"Duplicate [{current}] section")
            seen.append(current)
        elif current is not None:
            sections[current].append(line)
        elif line.strip():
            raise ProposalValidationError("Text before the first Semantic Beat section is not allowed")
    if seen != ["INITIAL", "BEATS"]:
        raise ProposalValidationError("Semantic Beat sections must be [INITIAL], [BEATS].")
    characters = tuple(anchor_model.aliases.values())
    initial_raw: dict[str, dict[str, str]] = {}; actor: str | None = None
    for line in sections["INITIAL"]:
        stripped = line.strip()
        if not stripped: continue
        known = next((name for name in characters if speaker_key(name) == speaker_key(stripped)), None)
        if known:
            if known in initial_raw: raise ProposalValidationError(f"Duplicate [INITIAL] actor {known}")
            initial_raw[known] = {}; actor = known; continue
        if _TARGET.fullmatch(stripped) and ":" not in stripped:
            raise ProposalValidationError(f"Unknown [INITIAL] performance actor {stripped}")
        match = _FIELD.fullmatch(stripped)
        if actor is None or not match: raise ProposalValidationError(f"Malformed [INITIAL] line: {stripped}")
        field, value = match.group(1).lower(), match.group(2)
        if field not in {"affect", "focus", "acting", "head"}:
            raise ProposalValidationError(f"{actor}: Unknown initial field {field}")
        if field in initial_raw[actor]: raise ProposalValidationError(f"{actor}: Duplicate initial field {field}")
        initial_raw[actor][field] = value
    missing = [name for name in characters if name not in initial_raw]
    if missing: raise ProposalValidationError("[INITIAL] requires one explicit actor block for: " + ", ".join(missing))
    initial: dict[str, dict[str, Any]] = {}
    for name in characters:
        row = initial_raw[name]
        if not row.get("affect"): raise ProposalValidationError(f"{name} initial: affect is required")
        if not row.get("focus"): raise ProposalValidationError(f"{name} initial: focus is required")
        if not row.get("acting", "").strip(): raise ProposalValidationError(f"{name} initial: acting is required")
        head = row.get("head", "HEAD-NONE").strip().upper()
        if head not in HEAD_VALUES: raise ProposalValidationError(f'{name} initial: Invalid head value "{head}"')
        initial[name] = {"affect": _affect(row["affect"], label=f"{name} initial", vocabulary=vocabulary, allow_none=False), "focus": _focus(row["focus"], label=f"{name} initial", characters=characters), "acting": row["acting"].strip(), "head": head}
    raw_beats: list[dict[str, str]] = []; beat: dict[str, str] | None = None
    for line in sections["BEATS"]:
        stripped = line.strip()
        if not stripped: continue
        if _EVENT_ID.fullmatch(stripped):
            beat = {"event_id": stripped.upper()}; raw_beats.append(beat); continue
        match = _FIELD.fullmatch(stripped)
        if beat is None or not match: raise ProposalValidationError(f"Malformed [BEATS] line: {stripped}")
        field, value = match.group(1).lower(), match.group(2)
        if field not in {"actor", "trigger", "acting", "affect", "focus", "eye_action", "head", "blink"}:
            raise ProposalValidationError(f"{beat['event_id']}: Unknown field {field}")
        if field in beat: raise ProposalValidationError(f"{beat['event_id']}: Duplicate field {field}")
        beat[field] = value
    ids = [row["event_id"] for row in raw_beats]
    if len(ids) != len(set(ids)): raise ProposalValidationError("Event IDs must be unique")
    anchors = {item.anchor_id for item in anchor_model.anchors}; beats: list[dict[str, Any]] = []
    for row in raw_beats:
        event_id = row["event_id"]
        if any(not row.get(key, "").strip() for key in ("actor", "trigger", "acting")):
            raise ProposalValidationError(f"{event_id}: actor, trigger, and acting are required")
        event_actor = next((name for name in characters if speaker_key(name) == speaker_key(row["actor"])), None)
        if event_actor is None: raise ProposalValidationError(f'{event_id}: Unknown performance actor "{row["actor"]}"')
        anchor_id = row["trigger"].strip().lower()
        if not _ANCHOR.fullmatch(anchor_id) or anchor_id not in anchors: raise ProposalValidationError(f'{event_id}: Unknown trigger anchor "{row["trigger"]}"')
        out: dict[str, Any] = {"event_id": event_id, "actor": event_actor, "anchor_id": anchor_id, "acting": row["acting"].strip()}
        if "affect" in row: out["affect"] = _affect(row["affect"], label=event_id, vocabulary=vocabulary, allow_none=True)
        if "focus" in row: out["focus"] = _focus(row["focus"], label=event_id, characters=characters)
        if "eye_action" in row: out["eye_action"] = _eye_action(row["eye_action"], label=event_id, characters=characters)
        if "focus" in out and "eye_action" in out:
            raise ProposalValidationError(f"{event_id}: focus and eye_action cannot both be authored in the same Semantic Beat")
        if "head" in row:
            head = row["head"].strip().upper()
            if head not in HEAD_VALUES: raise ProposalValidationError(f'{event_id}: Invalid head value "{head}"')
            out["head"] = head
        if "blink" in row:
            blink = row["blink"].strip().upper()
            if blink not in BLINK_VALUES: raise ProposalValidationError(f'{event_id}: Invalid blink value "{blink}"')
            out["blink"] = blink
        if len(out) == 4:
            # A syntactically valid acting-only beat is a harmless LLM no-op.
            # Preserve its source event ID in diagnostics but emit no executable event.
            continue
        beats.append(out)
    identities = [(row["actor"], row["anchor_id"]) for row in beats]
    if len(identities) != len(set(identities)): raise ProposalValidationError("Each actor may have at most one Semantic Beat at the same anchor")
    dropped = [row["event_id"] for row in raw_beats if row["event_id"] not in {beat["event_id"] for beat in beats}]
    warnings = [f"{event_id}: dropped acting-only beat because it contains no semantic changes" for event_id in dropped]
    return {"initial": initial, "beats": beats, "diagnostics": {"errors": [], "warnings": warnings}}
