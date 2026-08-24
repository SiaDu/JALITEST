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
from expregaze_jali.text_utils import iter_word_tokens

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
                end = _performative_blink_end(clean, start)
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


def _performative_blink_end(text: str, start: int) -> int:
    for token in iter_word_tokens(text):
        if int(token["end"]) > start:
            return int(token["end"])
    return min(start + 1, len(text))


def _spans_for_event(
    spans: list[dict[str, Any]], tag_type: str, event_start: int, event_end: int
) -> list[dict[str, Any]]:
    matches = [
        tag
        for tag in spans
        if tag.get("type") == tag_type
        and int(tag["span_start"]) < event_end
        and int(tag["span_end"]) > event_start
    ]
    return sorted(matches, key=lambda tag: (tag["span_start"], tag["order"]))


def _event_span(tag: dict[str, Any], event_start: int, event_end: int) -> dict[str, Any]:
    return {
        "source_tag": str(tag["id"]),
        "char_start": max(event_start, int(tag["span_start"])),
        "char_end": min(event_end, int(tag["span_end"])),
        "value": str(tag["value"]),
    }


def _affect_span(tag: dict[str, Any], event_start: int, event_end: int) -> dict[str, Any]:
    span = _event_span(tag, event_start, event_end)
    match = _AFFECT_PATTERN.match(span["value"].strip())
    if match:
        span.update({"state": match.group(1) or None, "intensity": float(match.group(2)) / 100.0})
    else:
        span.update({"state": span["value"] or None, "intensity": None})
    return span


def _gaze_span(tag: dict[str, Any], event_start: int, event_end: int) -> dict[str, Any]:
    span = _event_span(tag, event_start, event_end)
    mode, separator, target = span["value"].partition("-")
    span.update({"mode": mode or None, "target": target if separator and target else None})
    return span


def _head_span(tag: dict[str, Any], event_start: int, event_end: int) -> dict[str, Any]:
    span = _event_span(tag, event_start, event_end)
    span["involvement"] = HEAD_INVOLVEMENT.get(span["value"].strip().upper())
    return span


def _lid_state_span(tag: dict[str, Any], event_start: int, event_end: int) -> dict[str, Any]:
    span = _event_span(tag, event_start, event_end)
    try:
        number = float(span["value"])
        span["lid_state"] = int(number) if number.is_integer() else number
    except ValueError:
        span["lid_state"] = span["value"] or None
    return span


def _rationale_entry(tag: dict[str, Any], event_id: str, warnings: list[str]) -> dict[str, str | None]:
    reason = str(tag.get("reason", "")).strip() or None
    if reason is None:
        warnings.append(f"{event_id}: missing rationale for {tag['id']}")
    return {"source_tag": str(tag["id"]), "reason": reason}


def _target_character(context_pack: dict[str, Any] | None) -> str | None:
    if not context_pack:
        return None
    for key in ("target_character", "speaker", "character"):
        value = context_pack.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _exact_transcript_error(clean: str, context_pack: dict[str, Any] | None) -> str | None:
    if not context_pack or "exact_transcript" not in context_pack:
        return None
    expected = context_pack["exact_transcript"]
    if not isinstance(expected, str):
        return "context exact_transcript is not a string"
    if clean == expected:
        return None
    mismatch = next(
        (index for index, (actual, source) in enumerate(zip(clean, expected)) if actual != source),
        min(len(clean), len(expected)),
    )
    actual = clean[mismatch] if mismatch < len(clean) else "<end>"
    source = expected[mismatch] if mismatch < len(expected) else "<end>"
    return (
        f"exact transcript mismatch at character {mismatch}: "
        f"annotation={actual!r}, context={source!r}"
    )


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
    transcript_error = _exact_transcript_error(clean, context_pack)
    if transcript_error:
        errors.append(transcript_error)

    resolved_character = target_character or _target_character(context_pack)
    if resolved_character is None:
        warnings.append("target_character is missing; no explicit character was present in the context pack")

    intent_tags = [tag for tag in spans if tag.get("type") == "intent"]
    if not intent_tags:
        errors.append("no intent tags found; Performance Plan has no events")

    events: list[PerformancePlanEvent] = []
    for index, intent_tag in enumerate(intent_tags, start=1):
        event_id = f"E{index:02d}"
        start = int(intent_tag["span_start"])
        end = int(intent_tag["span_end"])
        visible = _spans_for_event(spans, "mask", start, end)
        hidden = _spans_for_event(spans, "heart", start, end)
        gaze = _spans_for_event(spans, "gaze", start, end)
        head = _spans_for_event(spans, "head_involvement", start, end)
        lid_state = _spans_for_event(spans, "lid_state", start, end)
        performative = _spans_for_event(spans, "performative_blink", start, end)
        suppression = _spans_for_event(spans, "blink_suppression", start, end)
        transcript = clean[start:end]

        events.append(
            {
                "event_id": event_id,
                "source_intent_tag": str(intent_tag["id"]),
                "span": {"text": transcript, "char_start": start, "char_end": end},
                "intent": str(intent_tag["value"]) or None,
                "affect": {
                    "visible": [_affect_span(tag, start, end) for tag in visible],
                    "hidden": [_affect_span(tag, start, end) for tag in hidden],
                },
                "gaze": [_gaze_span(tag, start, end) for tag in gaze],
                "head": [_head_span(tag, start, end) for tag in head],
                "lid_state": [_lid_state_span(tag, start, end) for tag in lid_state],
                "blink": {
                    "performative": [_event_span(tag, start, end) for tag in performative],
                    "suppression": [_event_span(tag, start, end) for tag in suppression],
                },
                "rationale": {
                    "intent": _rationale_entry(intent_tag, event_id, warnings),
                    "affect": {
                        "visible": [_rationale_entry(tag, event_id, warnings) for tag in visible],
                        "hidden": [_rationale_entry(tag, event_id, warnings) for tag in hidden],
                    },
                    "gaze": [_rationale_entry(tag, event_id, warnings) for tag in gaze],
                    "head": [_rationale_entry(tag, event_id, warnings) for tag in head],
                    "lid_state": [_rationale_entry(tag, event_id, warnings) for tag in lid_state],
                    "blink": {
                        "performative": [
                            _rationale_entry(tag, event_id, warnings) for tag in performative
                        ],
                        "suppression": [
                            _rationale_entry(tag, event_id, warnings) for tag in suppression
                        ],
                    },
                },
                "evidence": {"transcript": transcript},
                "locks": default_locks(),
            }
        )

    plan: PerformancePlan = {
        "schema_version": SCHEMA_VERSION,
        "sequence_id": sequence_id,
        "target_character": resolved_character,
        "source_annotation": str(parsed.get("path")) if parsed.get("path") else None,
        "acting_interpretation": str(parsed.get("analyze") or ""),
        "events": events,
        "diagnostics": {"errors": errors, "warnings": warnings},
    }
    assert_no_timing_fields(plan)
    return plan
