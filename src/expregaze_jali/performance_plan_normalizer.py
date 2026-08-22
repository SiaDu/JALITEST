from __future__ import annotations

import re
from typing import Any

from expregaze_jali.performance_plan_schema import (
    SCHEMA_VERSION,
    PerformancePlan,
    PerformancePlanEvent,
    assert_no_timing_fields,
    default_locks,
)

HEAD_INVOLVEMENT = {
    "NONE": 0.0,
    "LOW": 0.25,
    "MEDIUM": 0.5,
    "HIGH": 0.75,
    "FULL": 1.0,
}

_AFFECT_PATTERN = re.compile(r"^(.*?)-(-?\d+(?:\.\d+)?)$")


def _tag_spans(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    clean = str(parsed.get("clean_transcript", ""))
    tags = sorted(
        parsed.get("tags", []),
        key=lambda tag: (int(tag["position"]), int(tag["order"])),
    )
    by_type: dict[str, list[dict[str, Any]]] = {}
    for tag in tags:
        by_type.setdefault(str(tag["type"]), []).append(tag)

    spans: list[dict[str, Any]] = []
    for typed_tags in by_type.values():
        for index, tag in enumerate(typed_tags):
            start = int(tag["position"])
            explicit_end = tag.get("explicit_end")
            if explicit_end is not None and int(explicit_end) >= start:
                end = int(explicit_end)
            elif tag.get("type") == "performative_blink":
                end = min(start + 1, len(clean))
            elif index + 1 < len(typed_tags):
                end = int(typed_tags[index + 1]["position"])
            else:
                end = len(clean)
            spans.append(
                {
                    **tag,
                    "span_start": start,
                    "span_end": max(start, min(end, len(clean))),
                }
            )
    return sorted(spans, key=lambda tag: (tag["span_start"], tag["order"]))


def _relevant_tags(
    spans: list[dict[str, Any]], tag_type: str, event_start: int, event_end: int
) -> list[dict[str, Any]]:
    candidates = [
        tag
        for tag in spans
        if tag.get("type") == tag_type
        and int(tag["span_start"]) < event_end
        and int(tag["span_end"]) > event_start
    ]
    active = [
        tag
        for tag in candidates
        if int(tag["span_start"]) <= event_start < int(tag["span_end"])
    ]
    nested = [tag for tag in candidates if event_start <= int(tag["span_start"]) < event_end]
    ordered = active + nested
    seen: set[int] = set()
    unique: list[dict[str, Any]] = []
    for tag in ordered:
        order = int(tag["order"])
        if order not in seen:
            seen.add(order)
            unique.append(tag)
    return unique


def _choose_tag(
    spans: list[dict[str, Any]],
    tag_type: str,
    event_start: int,
    event_end: int,
    event_id: str,
    warnings: list[str],
) -> dict[str, Any] | None:
    relevant = _relevant_tags(spans, tag_type, event_start, event_end)
    if len(relevant) > 1:
        warnings.append(
            f"{event_id}: multiple {tag_type} values overlap the intent beat; using {relevant[0]['id']}"
        )
    return relevant[0] if relevant else None


def _affect(value: str | None) -> dict[str, Any]:
    if value is None:
        return {"state": None, "intensity": None}
    match = _AFFECT_PATTERN.match(value.strip())
    if not match:
        return {"state": value.strip() or None, "intensity": None}
    return {"state": match.group(1) or None, "intensity": float(match.group(2)) / 100.0}


def _gaze(value: str | None) -> dict[str, str | None]:
    if not value:
        return {"mode": None, "target": None}
    mode, separator, target = value.partition("-")
    return {"mode": mode or None, "target": target if separator and target else None}


def _lid_state(value: str | None) -> int | float | str | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return value or None
    return int(number) if number.is_integer() else number


def _target_character(context_pack: dict[str, Any] | None) -> str | None:
    if not context_pack:
        return None
    for key in ("target_character", "speaker", "character"):
        value = context_pack.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def normalize_performance_plan(
    parsed: dict[str, Any],
    *,
    sequence_id: str,
    context_pack: dict[str, Any] | None = None,
    target_character: str | None = None,
) -> PerformancePlan:
    """Deterministically normalize parsed actor tags into Performance Plan v0."""
    clean = str(parsed.get("clean_transcript", ""))
    spans = _tag_spans(parsed)
    parser_diagnostics = parsed.get("diagnostics", {})
    errors = list(parser_diagnostics.get("errors", []))
    warnings = list(parser_diagnostics.get("warnings", []))
    resolved_character = target_character or _target_character(context_pack)
    if resolved_character is None:
        warnings.append("target_character is missing; no explicit character was present in the context pack")

    intent_tags = [tag for tag in spans if tag.get("type") == "intent"]
    if not intent_tags:
        errors.append("no intent tags found; Performance Plan has no events")

    events: list[PerformancePlanEvent] = []
    semantic_types = (
        "mask",
        "heart",
        "gaze",
        "head_involvement",
        "lid_state",
        "performative_blink",
        "blink_suppression",
    )
    for index, intent_tag in enumerate(intent_tags, start=1):
        event_id = f"E{index:02d}"
        start = int(intent_tag["span_start"])
        end = int(intent_tag["span_end"])
        selected = {
            tag_type: _choose_tag(spans, tag_type, start, end, event_id, warnings)
            for tag_type in semantic_types
        }

        head_value = None
        head_tag = selected["head_involvement"]
        if head_tag is not None:
            head_value = HEAD_INVOLVEMENT.get(str(head_tag["value"]).strip().upper())

        rationale_tags = [intent_tag] + [tag for tag in selected.values() if tag is not None]
        rationale_parts = [
            f"{tag['id']}: {tag['reason']}"
            for tag in rationale_tags
            if str(tag.get("reason", "")).strip()
        ]
        for tag in rationale_tags:
            if not str(tag.get("reason", "")).strip():
                warnings.append(f"{event_id}: missing rationale for {tag['id']}")

        mask_tag = selected["mask"]
        heart_tag = selected["heart"]
        gaze_tag = selected["gaze"]
        lid_tag = selected["lid_state"]
        performative_tag = selected["performative_blink"]
        suppression_tag = selected["blink_suppression"]
        transcript = clean[start:end]
        events.append(
            {
                "event_id": event_id,
                "source_intent_tag": str(intent_tag["id"]),
                "span": {"text": transcript, "char_start": start, "char_end": end},
                "intent": str(intent_tag["value"]) or None,
                "affect": {
                    "visible": _affect(str(mask_tag["value"]) if mask_tag else None),
                    "hidden": _affect(str(heart_tag["value"]) if heart_tag else None),
                },
                "gaze": _gaze(str(gaze_tag["value"]) if gaze_tag else None),
                "head": {"involvement": head_value},
                "lid_state": _lid_state(str(lid_tag["value"]) if lid_tag else None),
                "blink": {
                    "performative": str(performative_tag["value"]) if performative_tag else None,
                    "suppression": str(suppression_tag["value"]) if suppression_tag else None,
                },
                "rationale": " | ".join(rationale_parts) or None,
                "evidence": {"transcript": transcript},
                "locks": default_locks(),
            }
        )

    plan: PerformancePlan = {
        "schema_version": SCHEMA_VERSION,
        "sequence_id": sequence_id,
        "target_character": resolved_character,
        "source_annotation": str(parsed.get("path")) if parsed.get("path") else None,
        "events": events,
        "diagnostics": {"errors": errors, "warnings": warnings},
    }
    assert_no_timing_fields(plan)
    return plan
