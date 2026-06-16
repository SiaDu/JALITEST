from __future__ import annotations

from typing import Any


def _split_mode_reason(value: str) -> tuple[str, str]:
    text = str(value).strip()
    if "-" not in text:
        return text.upper(), ""
    mode, reason = text.split("-", 1)
    return mode.strip().upper(), reason.strip()


def _resolved_start(event: dict[str, Any]) -> float | None:
    rt = event.get("resolved_time")
    if not rt:
        return None
    return float(rt["start"])


def _resolved_end(event: dict[str, Any]) -> float | None:
    rt = event.get("resolved_time")
    if not rt:
        return None
    return float(rt["end"])


def _clip_end_seconds(resolved_events: dict[str, Any], clip_end_frame: float | None, fps: float) -> float:
    if clip_end_frame is not None:
        return float(clip_end_frame) / float(fps)

    ends = [
        float(event["resolved_time"]["end"])
        for event in resolved_events.get("events", [])
        if event.get("resolved_time")
    ]
    return max(ends) if ends else 0.0


def _export_lid_state_events(resolved_events: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in resolved_events.get("events", []):
        if event.get("type") != "lid_state":
            continue
        value = int(float(str(event.get("value", "0")).strip()))
        out.append(
            {
                "id": event["id"],
                "type": "lid_state",
                "value": value,
                "text": event.get("text", ""),
                "reason": event.get("reason", ""),
                "span": event.get("span"),
                "resolved_time": event.get("resolved_time"),
            }
        )
    return out


def _export_performative_blinks(resolved_events: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in resolved_events.get("events", []):
        if event.get("type") != "performative_blink":
            continue
        mode, subtype = _split_mode_reason(str(event.get("value", "")))
        rt = event.get("resolved_time")
        out.append(
            {
                "id": event["id"],
                "type": "blink",
                "class": "performative",
                "mode": mode,
                "subtype": subtype,
                "text": event.get("text", ""),
                "reason": event.get("reason", ""),
                "time": float(rt["start"]) if rt else None,
                "span": event.get("span"),
                "resolved_time": rt,
            }
        )
    return out


def _export_blink_suppression_intervals(
    resolved_events: dict[str, Any],
    clip_end_sec: float,
) -> list[dict[str, Any]]:
    bs_events = [
        event
        for event in resolved_events.get("events", [])
        if event.get("type") == "blink_suppression" and event.get("resolved_time")
    ]
    bs_events = sorted(bs_events, key=lambda event: float(event["resolved_time"]["start"]))

    intervals: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None

    for event in bs_events:
        mode, detail = _split_mode_reason(str(event.get("value", "")))
        start = float(event["resolved_time"]["start"])

        if mode in {"SUPPRESS", "SUPPRESS_BLINK", "ON"}:
            if active is not None:
                active["end"] = start
                intervals.append(active)
            active = {
                "id": event["id"],
                "type": "blink_suppression",
                "mode": "SUPPRESS",
                "detail": detail,
                "start": start,
                "end": float(event["resolved_time"].get("end", clip_end_sec)),
                "text": event.get("text", ""),
                "reason": event.get("reason", ""),
                "source_event_id": event["id"],
            }
        elif mode in {"ALLOW", "ALLOW_BLINK", "OFF"}:
            if active is not None:
                active["end"] = start
                intervals.append(active)
                active = None

    if active is not None:
        active["end"] = clip_end_sec
        intervals.append(active)

    return [
        interval
        for interval in intervals
        if interval["end"] > interval["start"]
    ]


def _in_intervals(time_sec: float, intervals: list[dict[str, Any]]) -> bool:
    return any(float(item["start"]) <= time_sec <= float(item["end"]) for item in intervals)


def _too_close(time_sec: float, existing: list[dict[str, Any]], min_gap_sec: float) -> bool:
    for item in existing:
        item_time = item.get("time")
        if item_time is not None and abs(float(item_time) - time_sec) < min_gap_sec:
            return True
    return False


def _make_regulatory_blink(
    index: int,
    time_sec: float,
    trigger: str,
    source_event_id: str,
    subtle: bool = False,
) -> dict[str, Any]:
    return {
        "id": f"rb{index:03d}",
        "type": "blink",
        "class": "regulatory",
        "mode": "VERY_SUBTLE_BLINK" if subtle else "SINGLE_BLINK",
        "trigger": trigger,
        "source_event_id": source_event_id,
        "time": time_sec,
        "duration_frames": 3 if subtle else 4,
        "closure": 4 if subtle else 7,
        "reason": (
            "Very subtle blink inserted after a long no-blink gap."
            if subtle
            else f"Regulatory blink for {trigger}."
        ),
    }


def generate_regulatory_blinks(
    resolved_events: dict[str, Any],
    performative_blinks: list[dict[str, Any]],
    blink_suppression_intervals: list[dict[str, Any]],
    fps: float,
    clip_end_sec: float,
    min_gap_frames: int = 15,
    long_gap_seconds: float = 5.0,
    gaze_blink_offset_frames: int = 1,
) -> list[dict[str, Any]]:
    """
    Rule-based blinks, generated after TextGrid timing resolve.

    Rules:
      1. gaze event: blink at transition start or inside transition.
      2. mask / heart / lid_state state change: blink as facial reset.
      3. long no-blink gap: add a very subtle blink after 4-6 seconds.
      4. never generate inside blink_suppression intervals.
      5. respect cooldown against both performative and regulatory blinks.
    """
    min_gap_sec = float(min_gap_frames) / float(fps)
    offset_sec = float(gaze_blink_offset_frames) / float(fps)

    existing: list[dict[str, Any]] = [
        blink for blink in performative_blinks if blink.get("time") is not None
    ]
    regulatory: list[dict[str, Any]] = []

    candidates: list[tuple[float, str, str]] = []
    for event in resolved_events.get("events", []):
        if not event.get("resolved_time"):
            continue
        event_type = event.get("type")
        if event_type == "gaze":
            candidates.append(
                (
                    max(0.0, float(event["resolved_time"]["start"]) + offset_sec),
                    "gaze_change",
                    str(event.get("id", "")),
                )
            )
        elif event_type in {"mask", "heart", "lid_state"}:
            candidates.append(
                (
                    max(0.0, float(event["resolved_time"]["start"])),
                    f"{event_type}_change",
                    str(event.get("id", "")),
                )
            )

    next_idx = 1
    for time_sec, trigger, source_id in sorted(candidates, key=lambda item: item[0]):
        if _in_intervals(time_sec, blink_suppression_intervals):
            continue
        if _too_close(time_sec, existing + regulatory, min_gap_sec):
            continue
        blink = _make_regulatory_blink(next_idx, time_sec, trigger, source_id)
        regulatory.append(blink)
        next_idx += 1

    all_known = sorted(existing + regulatory, key=lambda item: float(item["time"]))
    last_time = 0.0
    for item in all_known:
        time_sec = float(item["time"])
        while time_sec - last_time > long_gap_seconds:
            subtle_time = last_time + long_gap_seconds
            if (
                subtle_time < clip_end_sec
                and not _in_intervals(subtle_time, blink_suppression_intervals)
                and not _too_close(subtle_time, existing + regulatory, min_gap_sec)
            ):
                blink = _make_regulatory_blink(
                    next_idx,
                    subtle_time,
                    "long_no_blink_gap",
                    "auto",
                    subtle=True,
                )
                regulatory.append(blink)
                next_idx += 1
                last_time = subtle_time
            else:
                break
        last_time = max(last_time, time_sec)

    while clip_end_sec - last_time > long_gap_seconds:
        subtle_time = last_time + long_gap_seconds
        if (
            subtle_time < clip_end_sec
            and not _in_intervals(subtle_time, blink_suppression_intervals)
            and not _too_close(subtle_time, existing + regulatory, min_gap_sec)
        ):
            blink = _make_regulatory_blink(
                next_idx,
                subtle_time,
                "long_no_blink_gap",
                "auto",
                subtle=True,
            )
            regulatory.append(blink)
            next_idx += 1
            last_time = subtle_time
        else:
            break

    return sorted(regulatory, key=lambda item: float(item["time"]))


def export_eye_performance_events(
    resolved_events: dict[str, Any],
    clip_name: str = "",
    fps: float = 30.0,
    clip_end_frame: float | None = None,
    generate_regulatory: bool = True,
    regulatory_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export lid_state, performative blink, blink suppression, and generated regulatory blink events."""
    config = regulatory_config or {}
    clip_end_sec = _clip_end_seconds(resolved_events, clip_end_frame, fps)

    lid_state_events = _export_lid_state_events(resolved_events)
    performative_blink_events = _export_performative_blinks(resolved_events)
    blink_suppression_events = _export_blink_suppression_intervals(resolved_events, clip_end_sec)

    regulatory_blink_events = (
        generate_regulatory_blinks(
            resolved_events=resolved_events,
            performative_blinks=performative_blink_events,
            blink_suppression_intervals=blink_suppression_events,
            fps=fps,
            clip_end_sec=clip_end_sec,
            min_gap_frames=int(config.get("min_gap_frames", 15)),
            long_gap_seconds=float(config.get("long_gap_seconds", 5.0)),
            gaze_blink_offset_frames=int(config.get("gaze_blink_offset_frames", 1)),
        )
        if generate_regulatory
        else []
    )

    return {
        "clip_name": clip_name,
        "fps": fps,
        "clip_end_frame": clip_end_frame,
        "clip_end_seconds": clip_end_sec,
        "lid_state_events": lid_state_events,
        "performative_blink_events": performative_blink_events,
        "blink_suppression_events": blink_suppression_events,
        "regulatory_blink_events": regulatory_blink_events,
        "diagnostics": {
            "lid_state_count": len(lid_state_events),
            "performative_blink_count": len(performative_blink_events),
            "blink_suppression_count": len(blink_suppression_events),
            "regulatory_blink_count": len(regulatory_blink_events),
            "source_diagnostics": resolved_events.get("diagnostics", {}),
        },
    }
