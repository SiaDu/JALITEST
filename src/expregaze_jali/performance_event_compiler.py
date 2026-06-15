from __future__ import annotations

from typing import Any


def _trim_span(text: str, start: int, end: int) -> tuple[int, int, str]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end, text[start:end]


def compile_state_change_events(parsed: dict[str, Any]) -> dict[str, Any]:
    """Convert state-change tags into structured typed text-span events."""
    clean = parsed.get("clean_transcript", "")
    tags = sorted(parsed.get("tags", []), key=lambda tag: (tag["position"], tag["order"]))
    events: list[dict[str, Any]] = []

    for tag_type in ("gaze", "mask", "heart"):
        typed_tags = [tag for tag in tags if tag.get("type") == tag_type]
        for idx, tag in enumerate(typed_tags):
            raw_start = int(tag["position"])
            raw_end = int(typed_tags[idx + 1]["position"]) if idx + 1 < len(typed_tags) else len(clean)
            start, end, span_text = _trim_span(clean, raw_start, raw_end)
            events.append(
                {
                    "id": tag["id"],
                    "type": tag_type,
                    "value": tag["value"],
                    "text": span_text,
                    "reason": tag.get("reason", ""),
                    "span": {
                        "start": start,
                        "end": end,
                        "raw_start": raw_start,
                        "raw_end": raw_end,
                    },
                    "order": tag["order"],
                }
            )

    events = sorted(events, key=lambda event: (event["span"]["start"], event["order"], event["type"]))
    return {
        "clean_transcript": clean,
        "events": events,
        "gaze": [event for event in events if event["type"] == "gaze"],
        "mask": [event for event in events if event["type"] == "mask"],
        "heart": [event for event in events if event["type"] == "heart"],
        "diagnostics": {},
    }

