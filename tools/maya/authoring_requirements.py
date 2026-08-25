"""Pure-Python separation of semantic authoring from Maya execution setup."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable


DIRECTIONS = {"DOWN", "DOWN_LEFT", "DOWN_RIGHT", "UP", "UP_LEFT", "UP_RIGHT", "LEFT", "RIGHT"}


def _target(value: str) -> str:
    raw = str(value or "").strip().upper()
    if "-" in raw:
        raw = raw.split("-", 1)[1]
    for prefix in ("CHARACTER_", "OBJECT_", "PROP_", "PERSON_"):
        if raw.startswith(prefix):
            return raw[len(prefix):]
    return raw


def required_look_at_targets(plan: dict[str, Any]) -> list[str]:
    """Return unique scene-mapped semantic targets, never directions or NONE."""
    values: list[str] = []
    for event in plan.get("events", []):
        for gaze in event.get("gaze", []) if isinstance(event, dict) else []:
            values.append(str(gaze.get("target") or gaze.get("value") or ""))
    for phrase in plan.get("phrases", []):
        for state in (phrase.get("states") or {}).values() if isinstance(phrase, dict) else []:
            values.append(str((state or {}).get("gaze") or ""))
    result: list[str] = []
    for value in values:
        target = _target(value)
        if target and target not in DIRECTIONS | {"NONE", "UNRESOLVED", "A", "B"} and target not in result:
            result.append(target)
    return result


def refresh_look_at_mappings(required: Iterable[str], existing: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Create visible required rows while retaining node choices by semantic name."""
    known = {str(row.get("semantic_target") or "").strip().upper(): str(row.get("maya_node") or "") for row in existing}
    return [{"semantic_target": name, "maya_node": known.get(name.upper(), "")} for name in required]


def animation_setup_issues(
    *, plan: dict[str, Any] | None, audio_folder: str, characters: Iterable[dict[str, Any]],
    look_at_mappings: Iterable[dict[str, Any]], node_exists: Callable[[str], bool],
) -> list[str]:
    if not plan:
        return ["Performance Plan: generate or load a plan first"]
    issues: list[str] = []
    if not str(audio_folder).strip() or not Path(str(audio_folder)).is_dir():
        issues.append("Audio: input audio folder not selected or does not exist")
    for row in characters:
        name, node = str(row.get("script_name") or "").strip(), str(row.get("maya_node") or "").strip()
        if not node:
            issues.append(f"Character: {name or 'active character'}: Maya rig/node not selected")
        elif not node_exists(node):
            issues.append(f"Character: {name}: Maya node does not exist: {node}")
    mapped = {str(row.get("semantic_target") or "").strip().upper(): str(row.get("maya_node") or "").strip() for row in look_at_mappings}
    for name in required_look_at_targets(plan):
        node = mapped.get(name, "")
        if not node:
            issues.append(f"Look-at targets: {name}: Maya node not selected")
        elif not node_exists(node):
            issues.append(f"Look-at targets: {name}: Maya node does not exist: {node}")
    return issues
