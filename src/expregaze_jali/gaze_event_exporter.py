from __future__ import annotations

from typing import Any


def _split_gaze_value(value: str) -> tuple[str, str]:
    if "-" not in value:
        return value.strip(), ""
    mode, target = value.split("-", 1)
    return mode.strip(), target.strip()


def export_gaze_events(resolved_events: dict[str, Any], clip_name: str = "") -> dict[str, Any]:
    """Export resolved gaze events for the Maya adapter."""
    events: list[dict[str, Any]] = []
    for event in resolved_events.get("events", []):
        if event.get("type") != "gaze":
            continue
        mode, target = _split_gaze_value(str(event.get("value", "")))
        events.append(
            {
                "id": event["id"],
                "type": "gaze",
                "mode": mode,
                "target": target,
                "text": event.get("text", ""),
                "reason": event.get("reason", ""),
                "span": event.get("span"),
                "resolved_time": event.get("resolved_time"),
            }
        )

    return {
        "clip_name": clip_name,
        "events": events,
        "diagnostics": resolved_events.get("diagnostics", {}),
    }

