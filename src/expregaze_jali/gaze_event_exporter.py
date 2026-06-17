from __future__ import annotations

from typing import Any

from expregaze_jali.target_resolution import resolve_gaze_target


def _split_gaze_value(value: str) -> tuple[str, str]:
    if "-" not in value:
        return value.strip(), ""
    mode, target = value.split("-", 1)
    return mode.strip(), target.strip()


def export_gaze_events(
    resolved_events: dict[str, Any],
    clip_name: str = "",
    context_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export resolved gaze events for the Maya adapter."""
    events: list[dict[str, Any]] = []
    generic_targets: list[dict[str, Any]] = []
    unresolved_targets: list[dict[str, Any]] = []

    for event in resolved_events.get("events", []):
        if event.get("type") != "gaze":
            continue
        mode, target = _split_gaze_value(str(event.get("value", "")))
        target_resolution = resolve_gaze_target(target, context_pack)

        exported = {
            "id": event["id"],
            "type": "gaze",
            "mode": mode,
            "target": target,
            **target_resolution,
            "text": event.get("text", ""),
            "reason": event.get("reason", ""),
            "span": event.get("span"),
            "resolved_time": event.get("resolved_time"),
        }
        events.append(exported)

        if target_resolution.get("target_needs_resolution"):
            unresolved_targets.append(
                {
                    "id": event.get("id"),
                    "target": target,
                    "text": event.get("text", ""),
                    "reason": event.get("reason", ""),
                    "target_resolution_source": target_resolution.get("target_resolution_source"),
                }
            )
        if str(target).upper() in {"OBJECT", "PROP", "THING", "PERSON", "CHARACTER"}:
            generic_targets.append(
                {
                    "id": event.get("id"),
                    "target": target,
                    "text": event.get("text", ""),
                    "reason": event.get("reason", ""),
                }
            )

    diagnostics = dict(resolved_events.get("diagnostics", {}))
    diagnostics["generic_targets"] = generic_targets
    diagnostics["unresolved_targets"] = unresolved_targets

    return {
        "clip_name": clip_name,
        "events": events,
        "diagnostics": diagnostics,
    }
