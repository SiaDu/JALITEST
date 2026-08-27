"""Pure-Python JSON helpers for the Maya Performance Plan Editor.

This module deliberately has no Maya, Qt, or backend-package imports so it can
be exercised by the normal Python test suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HEAD_VALUE_BY_INVOLVEMENT = {
    0.0: "NONE",
    0.25: "LOW",
    0.5: "MEDIUM",
    0.75: "HIGH",
    1.0: "FULL",
}


def load_performance_plan(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Performance Plan JSON must have an object at its root.")
    schema = value.get("schema_version")
    if schema in {"dual_performance_plan_v0", "dual_performance_plan_v1", "dual_performance_plan_v2"}:
        if schema == "dual_performance_plan_v2":
            characters = value.get("characters")
            tracks = value.get("tracks")
            if not isinstance(characters, list) or len(characters) != 2:
                raise ValueError("Dual v2 Performance Plan JSON must contain exactly two named characters.")
            if not isinstance(tracks, dict) or set(tracks) != set(characters):
                raise ValueError("Dual v2 Performance Plan JSON must contain name-keyed tracks.")
            if not all(isinstance(tracks[name], list) for name in characters):
                raise ValueError("Every dual v2 character track must be a list.")
            initial_states = value.get("initial_states", {})
            if not isinstance(initial_states, dict) or not set(initial_states) <= set(characters):
                raise ValueError("Dual v2 initial_states must be name-keyed by plan characters.")
            return value
        if not isinstance(value.get("phrases"), list):
            raise ValueError("Dual Performance Plan JSON must contain a phrases list.")
        if schema == "dual_performance_plan_v1":
            characters = value.get("characters")
            if not isinstance(characters, list) or len(characters) != 2:
                raise ValueError("Dual v1 Performance Plan JSON must contain exactly two named characters.")
    elif not isinstance(value.get("events"), list):
        raise ValueError("Performance Plan JSON must contain an events list.")
    return value


def default_edited_path(path: str | Path) -> Path:
    source = Path(path)
    if source.stem.endswith("_edited"):
        return source
    return source.with_name(f"{source.stem}_edited{source.suffix}")


def save_performance_plan(plan: dict[str, Any], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def save_animation_runtime_plan(
    score_model: Any, score_text: str, path: str | Path
) -> dict[str, Any]:
    """Validate/apply the current score and persist the exact canonical runtime plan."""
    plan = score_model.apply(score_text)
    save_performance_plan(plan, path)
    return plan


def set_event_intent(event: dict[str, Any], intent: str) -> None:
    event["intent"] = intent


def _format_scaled_intensity(intensity: float) -> str:
    scaled = round(float(intensity) * 100)
    return str(int(scaled))


def update_affect_span(span: dict[str, Any], state: str, intensity: float | None) -> None:
    clean_state = state.strip()
    span["state"] = clean_state or None
    span["intensity"] = intensity
    span["value"] = (
        f"{clean_state}-{_format_scaled_intensity(intensity)}"
        if clean_state and intensity is not None
        else clean_state
    )


def update_gaze_span(span: dict[str, Any], mode: str, target: str) -> None:
    clean_mode = mode.strip()
    clean_target = target.strip()
    span["mode"] = clean_mode or None
    span["target"] = clean_target or None
    span["value"] = f"{clean_mode}-{clean_target}" if clean_target else clean_mode


def update_head_span(span: dict[str, Any], involvement: float | None) -> None:
    span["involvement"] = involvement
    if involvement is None:
        span["value"] = ""
        return
    for numeric, label in HEAD_VALUE_BY_INVOLVEMENT.items():
        if abs(float(involvement) - numeric) < 1e-9:
            span["value"] = label
            return
    span["value"] = format(float(involvement), ".12g")


def update_lid_state_span(span: dict[str, Any], lid_state: float | None) -> None:
    span["lid_state"] = lid_state
    if lid_state is None:
        span["value"] = ""
    elif float(lid_state).is_integer():
        span["value"] = str(int(lid_state))
    else:
        span["value"] = format(float(lid_state), ".12g")


def update_blink_span(span: dict[str, Any], value: str) -> None:
    span["value"] = value.strip()


def set_event_locks(event: dict[str, Any], locks: dict[str, bool]) -> None:
    event_locks = event.setdefault("locks", {})
    for key in ("intent", "affect", "gaze", "head", "blink"):
        event_locks[key] = bool(locks.get(key, False))
