"""Pure actor-specific calibration helpers for dual gaze."""
from __future__ import annotations
from typing import Any, Iterable


def display_target(target: str, characters: dict[str, str]) -> str:
    raw = str(target).strip()
    return str(characters.get(raw.upper()) or raw.removeprefix("OBJECT_").removeprefix("PROP_").removeprefix("CHARACTER_")).strip()


def required_calibration_pairs(plan: dict[str, Any]) -> list[tuple[str, str]]:
    characters = plan.get("characters") or {}
    pairs: list[tuple[str, str]] = []
    for phrase in plan.get("phrases", []):
        for actor, state in (phrase.get("states") or {}).items():
            gaze = str((state or {}).get("gaze") or "")
            mode, _, target = gaze.partition("-")
            if mode not in {"GAZE", "GLANCE"} or not target:
                continue
            pair = (str(actor), target)
            if pair not in pairs: pairs.append(pair)
    return pairs


def calibration_key(actor: str, target: str) -> str:
    return f"{actor}->{target}"


def calibration_map(rows: Iterable[dict[str, Any]]) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for row in rows:
        key = calibration_key(str(row.get("actor_alias") or ""), str(row.get("target_alias") or ""))
        value = row.get("eye_stare_world_position")
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            continue
        result[key] = [float(item) for item in value]
    return result
