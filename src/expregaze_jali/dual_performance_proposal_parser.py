"""Strict parser for one shared dual-character semantic proposal."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from expregaze_jali.performance_proposal_parser import (
    BLINK_VALUES,
    HEAD_VALUES,
    LID_VALUES,
    REQUIRED_FIELDS,
    SUPPRESSION_VALUES,
    ProposalValidationError,
    SemanticVocabulary,
    _normalize_affect,
    _normalize_gaze,
    _intent_reason_looks_like_label,
    normalize_intent,
)


STATE_FIELDS = REQUIRED_FIELDS[2:]
_SECTION = re.compile(r"^\[(ANALYZE|PERFORMANCE|REASONS)\]\s*$", re.IGNORECASE)
_PROPOSAL_ID = re.compile(r"^S\d+$", re.IGNORECASE)
_FIELD = re.compile(r"^(?:(A|B)\.)?([a-z_]+)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_REASON_FIELD = re.compile(r"^(?:([a-z]+)\.)?([a-z_]+)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_START = re.compile(r"^w\d{4,}$", re.IGNORECASE)
_REASON = re.compile(r"^(S\d+)\.(?:([a-z]+)\.)?([a-z_]+)\s*:\s*(.+?)\s*$", re.IGNORECASE)


def _normalize_state(
    raw: dict[str, str], *, phrase_id: str, alias: str, vocabulary: SemanticVocabulary
) -> dict[str, Any]:
    missing = [field for field in STATE_FIELDS if field not in raw]
    if missing:
        raise ProposalValidationError(
            f"{phrase_id}: Missing required {alias} fields: {', '.join(missing)}"
        )
    head = raw["head"].strip().upper()
    if head not in HEAD_VALUES:
        raise ProposalValidationError(f'{phrase_id}: Unknown {alias}.head value "{raw["head"]}"')
    lid_text = raw["lid"].strip().upper()
    if lid_text == "NONE":
        lid = None
    else:
        try:
            lid = int(lid_text)
        except ValueError as exc:
            raise ProposalValidationError(
                f'{phrase_id}: Invalid {alias}.lid value "{raw["lid"]}"'
            ) from exc
        if lid not in LID_VALUES:
            raise ProposalValidationError(f"{phrase_id}: Unsupported {alias}.lid value {lid}")
    blink = raw["blink"].strip().upper()
    if blink not in BLINK_VALUES:
        raise ProposalValidationError(f'{phrase_id}: Unknown {alias}.blink value "{raw["blink"]}"')
    suppression = raw["blink_suppression"].strip().upper()
    if suppression not in SUPPRESSION_VALUES:
        raise ProposalValidationError(
            f'{phrase_id}: Unknown {alias}.blink_suppression value "{raw["blink_suppression"]}"'
        )
    return {
        "affect": _normalize_affect(
            raw["affect"], phrase_id=phrase_id, field=f"{alias}.affect",
            vocabulary=vocabulary.affect_states,
        ),
        "heart": _normalize_affect(
            raw["heart"], phrase_id=phrase_id, field=f"{alias}.heart",
            vocabulary=vocabulary.heart_states,
        ),
        "gaze": _normalize_gaze(raw["gaze"], phrase_id=phrase_id),
        "head": head,
        "lid": "NONE" if lid is None else lid,
        "blink": blink,
        "blink_suppression": suppression,
    }


def parse_dual_performance_proposal(
    source: str | Path, *, vocabulary: SemanticVocabulary
) -> dict[str, Any]:
    text = Path(source).read_text(encoding="utf-8") if isinstance(source, Path) else str(source)
    sections: dict[str, list[str]] = {"ANALYZE": [], "PERFORMANCE": [], "REASONS": []}
    current: str | None = None
    seen: list[str] = []
    for line in text.splitlines():
        match = _SECTION.fullmatch(line.strip())
        if match:
            current = match.group(1).upper()
            if current in seen:
                raise ProposalValidationError(f"Duplicate [{current}] section")
            seen.append(current)
        elif current is not None:
            sections[current].append(line)
        elif line.strip():
            raise ProposalValidationError("Text before [ANALYZE] is not allowed")
    if seen != ["ANALYZE", "PERFORMANCE", "REASONS"]:
        raise ProposalValidationError("Sections must be ordered [ANALYZE], [PERFORMANCE], [REASONS]")

    raw_phrases: list[dict[str, Any]] = []
    phrase: dict[str, Any] | None = None
    for line in sections["PERFORMANCE"]:
        stripped = line.strip()
        if not stripped:
            continue
        if _PROPOSAL_ID.fullmatch(stripped):
            phrase = {"proposal_id": stripped.upper(), "states": {"A": {}, "B": {}}}
            raw_phrases.append(phrase)
            continue
        match = _FIELD.fullmatch(stripped)
        if phrase is None or match is None:
            raise ProposalValidationError(f"Malformed [PERFORMANCE] line: {stripped}")
        alias, field, value = match.groups()
        field = field.lower()
        if alias is None:
            if field not in {"start", "intent"}:
                raise ProposalValidationError(f"{phrase['proposal_id']}: Unknown shared field {field}")
            destination = phrase
        else:
            alias = alias.upper()
            if field not in STATE_FIELDS:
                raise ProposalValidationError(f"{phrase['proposal_id']}: Unknown field {alias}.{field}")
            destination = phrase["states"][alias]
        if field in destination:
            raise ProposalValidationError(f"{phrase['proposal_id']}: Duplicate field {alias + '.' if alias else ''}{field}")
        destination[field] = value
    if not raw_phrases:
        raise ProposalValidationError("[PERFORMANCE] must contain at least one phrase")
    ids = [row["proposal_id"] for row in raw_phrases]
    if len(ids) != len(set(ids)):
        raise ProposalValidationError("Proposal phrase IDs must be unique")

    phrases: list[dict[str, Any]] = []
    for raw in raw_phrases:
        phrase_id = raw["proposal_id"]
        missing = [field for field in ("start", "intent") if field not in raw]
        if missing:
            raise ProposalValidationError(f"{phrase_id}: Missing required fields: {', '.join(missing)}")
        start = raw["start"].strip().lower()
        if not _START.fullmatch(start):
            raise ProposalValidationError(f'{phrase_id}: Invalid start anchor "{raw["start"]}"')
        try:
            intent = normalize_intent(raw["intent"])
        except ValueError as exc:
            raise ProposalValidationError(f"{phrase_id}: {exc}") from exc
        phrases.append({
            "proposal_id": phrase_id,
            "start_anchor": start,
            "intent": intent,
            "states": {
                alias: _normalize_state(
                    raw["states"][alias], phrase_id=phrase_id, alias=alias, vocabulary=vocabulary
                )
                for alias in ("A", "B")
            },
        })

    reasons: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    current_reason_phrase: str | None = None

    def store_reason(phrase_id: str, alias: str | None, field: str, reason: str) -> None:
        if phrase_id not in ids:
            raise ProposalValidationError(f"Reason refers to unknown phrase {phrase_id}")
        if alias is None:
            if field != "intent":
                raise ProposalValidationError(f"{phrase_id}: Unknown shared reason field {field}")
            destination = reasons.setdefault(phrase_id, {})
        else:
            alias = alias.upper()
            if alias not in {"A", "B"}:
                raise ProposalValidationError(f"{phrase_id}: Unknown rationale alias {alias}")
            if field not in STATE_FIELDS:
                raise ProposalValidationError(f"{phrase_id}: Unknown reason field {alias}.{field}")
            destination = reasons.setdefault(phrase_id, {}).setdefault(alias, {})
        if field in destination:
            prefix = f"{alias}." if alias else ""
            raise ProposalValidationError(f"{phrase_id}: Duplicate reason for {prefix}{field}")
        destination[field] = reason
        if alias is None and field == "intent" and _intent_reason_looks_like_label(reason):
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
            phrase_id, alias, field, reason = match.groups()
            store_reason(phrase_id.upper(), alias.upper() if alias else None, field.lower(), reason)
            continue
        field_match = _REASON_FIELD.fullmatch(stripped)
        if field_match is None or current_reason_phrase is None:
            raise ProposalValidationError(f"Malformed [REASONS] line: {stripped}")
        alias, field, reason = field_match.groups()
        if not reason:
            raise ProposalValidationError(f"Malformed [REASONS] line: {stripped}")
        store_reason(
            current_reason_phrase, alias.upper() if alias else None, field.lower(), reason
        )

    for phrase in phrases:
        reason = reasons.get(phrase["proposal_id"], {})
        if "intent" not in reason:
            warnings.append(f"{phrase['proposal_id']}: missing rationale for intent")
        for alias in ("A", "B"):
            alias_reasons = reason.get(alias, {})
            for field, value in phrase["states"][alias].items():
                active = (
                    (field == "head" and value != "NONE")
                    or (field not in {"head", "lid"} and value not in ("NONE", None))
                )
                if active and field not in alias_reasons:
                    warnings.append(f"{phrase['proposal_id']}: missing rationale for {alias}.{field}")
    return {
        "analyze": "\n".join(sections["ANALYZE"]).strip("\n"),
        "phrases": phrases,
        "reasons": reasons,
        "diagnostics": {"errors": [], "warnings": warnings},
    }
