from __future__ import annotations

from collections import defaultdict
from typing import Any


def _jali_tag_name(event_type: str) -> str:
    if event_type not in {"mask", "heart"}:
        raise ValueError(f"Unsupported JALI event type: {event_type}")
    return event_type


def _event_value(event: dict[str, Any]) -> str:
    value = str(event["value"])
    if event["type"] == "heart" and "-" in value:
        source, strength = value.rsplit("-", 1)
        if source and strength:
            return f"{source}-{strength}"
    return value


def _open_tag(event: dict[str, Any]) -> str:
    name = _jali_tag_name(event["type"])
    return f"<{name}={_event_value(event)}>"


def _close_tag(event: dict[str, Any]) -> str:
    name = _jali_tag_name(event["type"])
    return f"</{name}={_event_value(event)}>"


def export_jali_annotation(parsed: dict[str, Any], events: dict[str, Any]) -> str:
    """
    Export a JALI-compatible transcript annotation.

    Gaze tags are omitted. Mask and heart state changes are converted to paired
    tags while preserving tag values.
    """
    clean = parsed.get("clean_transcript", "")
    opens: dict[int, list[dict[str, Any]]] = defaultdict(list)
    closes: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for event in events.get("events", []):
        if event.get("type") not in {"mask", "heart"}:
            continue
        start = int(event["span"]["start"])
        end = int(event["span"]["end"])
        if end <= start:
            continue
        opens[start].append(event)
        closes[end].append(event)

    parts: list[str] = []
    for pos in range(len(clean) + 1):
        if pos in closes:
            for event in sorted(closes[pos], key=lambda item: item["order"], reverse=True):
                parts.append(_close_tag(event))
        if pos in opens:
            for event in sorted(opens[pos], key=lambda item: item["order"]):
                parts.append(_open_tag(event))
        if pos < len(clean):
            parts.append(clean[pos])

    return "".join(parts)
