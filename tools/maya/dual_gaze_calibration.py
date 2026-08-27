"""Pure actor-specific calibration helpers for dual gaze."""
from __future__ import annotations
from typing import Any, Iterable


def display_target(target: str, characters: dict[str, str] | list[str]) -> str:
    raw = str(target).strip()
    if isinstance(characters, dict):
        return str(characters.get(raw.upper()) or raw.removeprefix("OBJECT_").removeprefix("PROP_").removeprefix("CHARACTER_")).strip()
    return raw.removeprefix("OBJECT_").removeprefix("PROP_").removeprefix("CHARACTER_").strip()


def dual_actor_row_index(plan: dict[str, Any], actor_name: str) -> int:
    """Return the explicit Maya character-row index for a dual-plan actor."""
    characters = plan.get("characters")
    actor = str(actor_name).strip()
    if isinstance(characters, list):
        try:
            return characters.index(actor)
        except ValueError as exc:
            raise ValueError(f"Unknown v1 dual-plan actor {actor!r}.") from exc
    if isinstance(characters, dict):
        # v0 aliases are explicit plan keys, never inferred from dialogue order.
        alias = actor.upper()
        if alias not in characters:
            raise ValueError(f"Unknown v0 dual-plan actor {actor!r}.")
        try:
            return ("A", "B").index(alias)
        except ValueError as exc:
            raise ValueError(f"Unsupported v0 dual-plan actor {actor!r}.") from exc
    raise ValueError("Dual plan has no explicit characters mapping.")


def required_calibration_pairs(plan: dict[str, Any]) -> list[tuple[str, str]]:
    directions = {"UP", "DOWN", "LEFT", "RIGHT", "UP_LEFT", "UP_RIGHT", "DOWN_LEFT", "DOWN_RIGHT"}
    pairs: list[tuple[str, str]] = []
    if plan.get("schema_version") == "dual_performance_plan_v2":
        states = [
            (actor, state)
            for actor in plan.get("characters") or []
            for state in [((plan.get("initial_states") or {}).get(actor) or {})]
        ]
        states.extend(
            (actor, event.get("changes") or {})
            for actor in plan.get("characters") or []
            for event in (plan.get("tracks") or {}).get(actor, [])
            if isinstance(event, dict)
        )
    else:
        states = [
            (actor, state)
            for phrase in plan.get("phrases", [])
            for actor, state in (phrase.get("states") or {}).items()
        ]
    for actor, state in states:
            gaze = str((state or {}).get("gaze") or "")
            mode, _, target = gaze.partition("-")
            if mode not in {"GAZE", "GLANCE"} or not target or target == "NONE" or target in directions:
                continue
            pair = (str(actor), target)
            if pair not in pairs: pairs.append(pair)
    return pairs


def calibration_key(actor: str, target: str) -> str:
    return f"{actor}->{target}"


def calibration_map(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, list[float]]]:
    """Return only recapturable local-control target calibrations.

    Older rows which contain a world position alone are intentionally omitted:
    world coordinates cannot safely be reused as eyeStare translate values.
    """
    result: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        key = calibration_key(str(row.get("actor_alias") or ""), str(row.get("target_alias") or ""))
        value = row.get("eye_stare_translate")
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            continue
        result[key] = {"eye_stare_translate": [float(item) for item in value]}
        world = row.get("eye_stare_world_position")
        if isinstance(world, (list, tuple)) and len(world) == 3:
            result[key]["eye_stare_world_position"] = [float(item) for item in world]
    return result


def capture_target_pose_and_restore(
    eye_stare_node: str,
    both_eyes_node: str,
    *,
    baseline_translate_z: float,
    both_eyes_translate: Iterable[float],
    cmds_module: Any,
) -> dict[str, list[float]]:
    """Capture local eyeStare pose and immediately restore internal neutral."""
    local = [float(cmds_module.getAttr(f"{eye_stare_node}.translate{axis}")) for axis in "XYZ"]
    world = list(cmds_module.xform(eye_stare_node, query=True, worldSpace=True, translation=True))
    baseline_eyes = [float(value) for value in both_eyes_translate]
    if len(baseline_eyes) != 2:
        raise ValueError("both_eyes_translate must have two values.")
    cmds_module.setAttr(f"{eye_stare_node}.translateX", 0.0)
    cmds_module.setAttr(f"{eye_stare_node}.translateY", 0.0)
    cmds_module.setAttr(f"{eye_stare_node}.translateZ", float(baseline_translate_z))
    cmds_module.setAttr(f"{both_eyes_node}.translateX", baseline_eyes[0])
    cmds_module.setAttr(f"{both_eyes_node}.translateY", baseline_eyes[1])
    return {"eye_stare_translate": local, "eye_stare_world_position": [float(value) for value in world]}
