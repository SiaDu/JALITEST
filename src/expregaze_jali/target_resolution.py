from __future__ import annotations

from typing import Any

GENERIC_TARGETS = {"OBJECT", "PROP", "THING", "PERSON", "CHARACTER"}
ROLE_TARGETS = {"LISTENER", "SPEAKER", "PRIMARY_OBJECT"}
DEFAULT_DIRECTIONS = {
    "DOWN",
    "DOWN_LEFT",
    "DOWN_RIGHT",
    "UP",
    "UP_LEFT",
    "UP_RIGHT",
    "LEFT",
    "RIGHT",
    "CENTER",
}


def _upper_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        text = str(value).strip().upper().replace(" ", "_")
        if text and text not in out:
            out.append(text)
    return out


def _context_lists(context_pack: dict[str, Any] | None) -> tuple[list[str], list[str], list[str], dict[str, str]]:
    context_pack = context_pack or {}
    scene_targets = context_pack.get("scene_targets") or {}
    target_context = context_pack.get("target_context") or {}

    people = _upper_list(scene_targets.get("people"))
    objects = _upper_list(scene_targets.get("objects"))
    directions = _upper_list(scene_targets.get("directions")) or sorted(DEFAULT_DIRECTIONS)

    for value in _upper_list(target_context.get("listener_candidates")):
        if value not in people:
            people.append(value)
    for value in _upper_list(target_context.get("object_candidates")):
        if value not in objects:
            objects.append(value)
    for value in _upper_list(target_context.get("direction_targets")):
        if value not in directions:
            directions.append(value)

    role_map_raw = target_context.get("role_map") or {}
    role_map: dict[str, str] = {}
    if isinstance(role_map_raw, dict):
        for key, value in role_map_raw.items():
            role = str(key).strip().upper().replace(" ", "_")
            label = str(value).strip().upper().replace(" ", "_")
            if role and label:
                role_map[role] = label

    return people, objects, directions, role_map


def _typed_target_resolution(target: str, people: list[str], objects: list[str], directions: list[str]) -> dict[str, Any] | None:
    typed_prefixes = (
        ("CHARACTER_", "CHARACTER", people, "typed_character_target"),
        ("PERSON_", "CHARACTER", people, "typed_person_target"),
        ("OBJECT_", "OBJECT", objects, "typed_object_target"),
        ("PROP_", "OBJECT", objects, "typed_prop_target"),
        ("DIRECTION_", "DIRECTION", directions, "typed_direction_target"),
    )
    for prefix, role, known_values, source in typed_prefixes:
        if not target.startswith(prefix):
            continue
        label = target[len(prefix) :].strip("_")
        if not label:
            return None
        return {
            "target_role": role,
            "target_label": label,
            "target_needs_resolution": False,
            "target_resolution_source": f"{source}.scene_targets" if label in known_values else source,
        }
    return None


def resolve_gaze_target(raw_target: str, context_pack: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve semantic gaze target to a concrete target label when possible.

    This is not a hard validator. Unknown concrete targets are allowed and marked
    as explicit_unregistered_target rather than failed, because LLM may infer a
    useful target that the simple keyword context builder missed.
    """
    target = str(raw_target or "").strip().upper().replace(" ", "_")
    people, objects, directions, role_map = _context_lists(context_pack)

    if not target:
        return {
            "target_role": "UNKNOWN",
            "target_label": None,
            "target_needs_resolution": True,
            "target_resolution_source": "empty_target",
        }

    if target in role_map:
        return {
            "target_role": target,
            "target_label": role_map[target],
            "target_needs_resolution": False,
            "target_resolution_source": "target_context.role_map",
        }

    typed_resolution = _typed_target_resolution(target, people, objects, directions)
    if typed_resolution is not None:
        return typed_resolution

    if target == "OBJECT":
        if len(objects) == 1:
            return {
                "target_role": "OBJECT",
                "target_label": objects[0],
                "target_needs_resolution": False,
                "target_resolution_source": "single_object_hint",
            }
        return {
            "target_role": "OBJECT",
            "target_label": None,
            "target_needs_resolution": True,
            "target_resolution_source": "generic_object_target",
        }

    if target in GENERIC_TARGETS:
        return {
            "target_role": target,
            "target_label": None,
            "target_needs_resolution": True,
            "target_resolution_source": "generic_target",
        }

    if target in people:
        return {
            "target_role": "CHARACTER",
            "target_label": target,
            "target_needs_resolution": False,
            "target_resolution_source": "scene_targets.people",
        }

    if target in objects:
        return {
            "target_role": "OBJECT",
            "target_label": target,
            "target_needs_resolution": False,
            "target_resolution_source": "scene_targets.objects",
        }

    if target in set(directions) | DEFAULT_DIRECTIONS:
        return {
            "target_role": "DIRECTION",
            "target_label": target,
            "target_needs_resolution": False,
            "target_resolution_source": "direction_target",
        }

    if target in ROLE_TARGETS:
        return {
            "target_role": target,
            "target_label": None,
            "target_needs_resolution": True,
            "target_resolution_source": "unmapped_role_target",
        }

    return {
        "target_role": "EXPLICIT",
        "target_label": target,
        "target_needs_resolution": False,
        "target_resolution_source": "explicit_unregistered_target",
    }
