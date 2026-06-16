from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from expregaze_jali.maya_control_utils import (
    find_node_by_suffix,
    get_world_translation,
    key_translate,
    sec_to_frame,
    set_world_translation,
)


def _cmds():
    try:
        import maya.cmds as cmds  # type: ignore
        return cmds
    except Exception as exc:
        raise RuntimeError(
            "maya_apply_gaze must be run inside Autodesk Maya's Python environment."
        ) from exc


def _load_gaze_events(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    events = data.get("events", [])
    return [
        event
        for event in events
        if event.get("type") == "gaze" and event.get("resolved_time")
    ]


def _as_xyz(value: Any) -> list[float]:
    if isinstance(value, dict):
        if "position" in value:
            return _as_xyz(value["position"])
        if all(axis in value for axis in ("x", "y", "z")):
            return [float(value["x"]), float(value["y"]), float(value["z"])]

    if isinstance(value, (list, tuple)) and len(value) == 3:
        return [float(value[0]), float(value[1]), float(value[2])]

    raise ValueError(f"Cannot convert target value to xyz: {value!r}")


def _normalize_key(value: str) -> str:
    text = str(value).strip()
    return text if text.startswith("AIM_") else text.upper()


def _lookup_mapping(mapping: dict[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]
    upper_key = key.upper()
    for candidate, value in mapping.items():
        if str(candidate).upper() == upper_key:
            return value
    return None


def resolve_target_alias(target: str, target_aliases: dict[str, str] | None = None) -> str:
    """Resolve event target names such as LISTENER into Maya target aliases."""
    aliases = target_aliases or {}
    normalized = _normalize_key(target)
    alias = _lookup_mapping(aliases, normalized)
    return str(alias) if alias is not None else normalized


def clamp_position(position: Sequence[float], safe_bounds: dict[str, Any] | None = None) -> list[float]:
    """Clamp xyz to configured safe bounds."""
    xyz = _as_xyz(position)
    if not safe_bounds:
        return xyz

    out = list(xyz)
    for idx, axis in enumerate(("x", "y", "z")):
        bounds = safe_bounds.get(axis)
        if bounds is None:
            continue
        low, high = float(bounds[0]), float(bounds[1])
        out[idx] = min(max(out[idx], low), high)
    return out


def resolve_offset_position(base_position: Sequence[float], offset: Sequence[float]) -> list[float]:
    base = _as_xyz(base_position)
    delta = _as_xyz(offset)
    return [base[0] + delta[0], base[1] + delta[1], base[2] + delta[2]]


def _find_transform_node(node_name: str, cmds_module: Any) -> str:
    matches = cmds_module.ls(node_name, long=True) or cmds_module.ls(f"*:{node_name}", long=True) or []
    if not matches:
        raise RuntimeError(f"Target node not found: {node_name}")
    return matches[0]


def resolve_target_position(
    target: str,
    target_map: dict[str, Any],
    base_position: Sequence[float],
    direction_offsets: dict[str, Any] | None = None,
    target_aliases: dict[str, str] | None = None,
    safe_bounds: dict[str, Any] | None = None,
    cmds_module: Any | None = None,
) -> list[float]:
    """
    Resolve an event target into world-space xyz for eyeStare_world.

    `target_map` supports:
      {"AIM_listener": {"node": "listener_lookat_LOC"}}
      {"AIM_crystal": {"position": [1, 2, 126]}}
      {"DOWN": {"offset": [0, -8, 0]}}
    """
    target_key = resolve_target_alias(target, target_aliases)
    spec = _lookup_mapping(target_map, target_key)

    if spec is None:
        offset = _lookup_mapping(direction_offsets or {}, target_key)
        if offset is not None:
            spec = {"offset": offset}

    if spec is None:
        raise KeyError(
            f"No Maya gaze target entry for {target!r} resolved as {target_key!r}. "
            "Add it to maya_gaze.targets, maya_gaze.target_aliases, or maya_gaze.direction_offsets."
        )

    if isinstance(spec, str):
        if cmds_module is None:
            cmds_module = _cmds()
        node = _find_transform_node(spec, cmds_module)
        position = list(cmds_module.xform(node, query=True, worldSpace=True, translation=True))
        return clamp_position(position, safe_bounds)

    if isinstance(spec, dict):
        if "node" in spec:
            if cmds_module is None:
                cmds_module = _cmds()
            node = _find_transform_node(str(spec["node"]), cmds_module)
            position = list(cmds_module.xform(node, query=True, worldSpace=True, translation=True))
            return clamp_position(position, safe_bounds)

        if "position" in spec:
            return clamp_position(_as_xyz(spec["position"]), safe_bounds)

        if "offset" in spec:
            return clamp_position(resolve_offset_position(base_position, spec["offset"]), safe_bounds)

    return clamp_position(_as_xyz(spec), safe_bounds)


def _key_position(node: str, frame: int, xyz: list[float]) -> None:
    set_world_translation(node, xyz)
    key_translate(node, frame)


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        items = [item.strip() for item in text[1:-1].split(",") if item.strip()]
        return [_parse_scalar(item) for item in items]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    try:
        if any(char in text for char in (".", "e", "E")):
            return float(text)
        return int(text)
    except ValueError:
        return text.strip("\"'")


def _simple_yaml_load(text: str) -> dict[str, Any]:
    """Small YAML subset reader for Maya, where PyYAML may not be installed."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            raise ValueError(f"Unsupported YAML line: {raw_line!r}")

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if value:
            parent[key] = _parse_scalar(value)
        else:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))

    return root


def _load_yaml_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
    except ModuleNotFoundError:
        data = _simple_yaml_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def load_maya_gaze_config(path: str | Path) -> dict[str, Any]:
    """Load a `maya_gaze` YAML config and attach path context."""
    config_path = Path(path)
    data = _load_yaml_file(config_path)

    config = data.get("maya_gaze", data)
    if not isinstance(config, dict):
        raise ValueError(f"Invalid Maya gaze config: {path}")

    out = dict(config)
    out["_config_path"] = str(config_path)
    out["_config_dir"] = str(config_path.parent)
    repo_root = out.get("repo_root", ".")
    repo_root_path = Path(str(repo_root))
    inferred_project_root = config_path.parent.parent.parent
    if repo_root_path.is_absolute():
        project_root = repo_root_path
    else:
        project_root = inferred_project_root / repo_root_path
    out["_project_root"] = str(project_root.resolve() if project_root.exists() else project_root)
    return out


def resolve_repo_path(path_value: str | Path, config: dict[str, Any]) -> str:
    """Resolve a repo-relative config path against the configured repo root."""
    path = Path(path_value)
    if path.is_absolute():
        return str(path)
    project_root = Path(config.get("_project_root", "."))
    return str(project_root / path)


def resolve_maya_project_path(path_value: str | Path, config: dict[str, Any]) -> str:
    """Resolve a Maya project-relative path against `maya_project_root`."""
    path = Path(path_value)
    if path.is_absolute():
        return str(path)
    project_root_value = config.get("maya_project_root")
    if not project_root_value:
        raise KeyError("maya_project_root is required to resolve Maya project paths.")
    return str(Path(str(project_root_value)) / path)


def apply_gaze_events(
    gaze_events_path: str,
    target_map: dict,
    fps: float,
    direction_offsets: dict[str, Any] | None = None,
    target_aliases: dict[str, str] | None = None,
    safe_bounds: dict[str, Any] | None = None,
    base_position: Sequence[float] | None = None,
    eye_stare_node_suffix: str = "eyeStare_world",
) -> None:
    """
    Apply resolved gaze events to eyeStare_world.

    GAZE / AVERT hold target from event start to end. GLANCE keys previous
    gaze position -> glance target -> previous hold.
    """
    cmds = _cmds()

    events = _load_gaze_events(gaze_events_path)
    if not events:
        raise RuntimeError(f"No resolved gaze events found in: {gaze_events_path}")

    eye_stare = find_node_by_suffix(eye_stare_node_suffix)
    default_position = _as_xyz(base_position) if base_position is not None else get_world_translation(eye_stare)
    default_position = clamp_position(default_position, safe_bounds)
    current_hold_position = list(default_position)

    print(f"[INFO] Applying {len(events)} gaze events to {eye_stare}")
    print(f"[INFO] Base eyeStare position: {default_position}")

    for event in events:
        mode = str(event.get("mode", "")).upper()
        target = str(event.get("target", ""))
        resolved = event["resolved_time"]

        start_sec = float(resolved["start"])
        end_sec = float(resolved["end"])

        start_frame = sec_to_frame(start_sec, fps)
        end_frame = max(start_frame + 1, sec_to_frame(end_sec, fps))

        target_position = resolve_target_position(
            target=target,
            target_map=target_map,
            base_position=default_position,
            direction_offsets=direction_offsets,
            target_aliases=target_aliases,
            safe_bounds=safe_bounds,
            cmds_module=cmds,
        )
        target_key = resolve_target_alias(target, target_aliases)

        if mode in {"GAZE", "AVERT"}:
            _key_position(eye_stare, start_frame, target_position)
            _key_position(eye_stare, end_frame, target_position)
            current_hold_position = target_position

            print(
                f"[GAZE] {event.get('id')} {mode}-{target}->{target_key}: "
                f"{start_frame}->{end_frame}, pos={target_position}"
            )

        elif mode == "GLANCE":
            mid_frame = start_frame + max(1, (end_frame - start_frame) // 2)

            _key_position(eye_stare, start_frame, current_hold_position)
            _key_position(eye_stare, mid_frame, target_position)
            _key_position(eye_stare, end_frame, current_hold_position)

            print(
                f"[GLANCE] {event.get('id')} {mode}-{target}->{target_key}: "
                f"{start_frame}->{mid_frame}->{end_frame}, "
                f"out={target_position}, back={current_hold_position}"
            )

        else:
            print(f"[WARN] Unsupported gaze mode {mode!r}; skipped event {event.get('id')}")

    try:
        cmds.keyTangent(eye_stare, edit=True, inTangentType="spline", outTangentType="spline")
    except Exception:
        pass

    print("[DONE] Gaze overlay applied.")


def apply_gaze_events_from_config(config_path: str | Path) -> None:
    config = load_maya_gaze_config(config_path)
    gaze_events_path = resolve_repo_path(config["gaze_events_path"], config)
    apply_gaze_events(
        gaze_events_path=gaze_events_path,
        target_map=config.get("targets", {}),
        fps=float(config.get("fps", 24.0)),
        direction_offsets=config.get("direction_offsets", {}),
        target_aliases=config.get("target_aliases", {}),
        safe_bounds=config.get("safe_bounds", {}),
        base_position=config.get("base_position"),
        eye_stare_node_suffix=str(config.get("eye_stare_node_suffix", "eyeStare_world")),
    )
