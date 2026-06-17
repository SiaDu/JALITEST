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


def clamp_position(position: Sequence[float], bounds: dict[str, Any] | None = None) -> list[float]:
    """Clamp xyz to configured bounds.

    In v1.1 this is used only for direction offsets, not for world-space
    locator targets and not for the base eyeStare position.
    """
    xyz = _as_xyz(position)
    if not bounds:
        return xyz

    out = list(xyz)
    for idx, axis in enumerate(("x", "y", "z")):
        axis_bounds = bounds.get(axis)
        if axis_bounds is None:
            continue
        low, high = float(axis_bounds[0]), float(axis_bounds[1])
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
    direction_offset_bounds: dict[str, Any] | None = None,
    cmds_module: Any | None = None,
) -> list[float]:
    """
    Resolve an event target into world-space xyz for eyeStare_world.

    Important:
      - Locator targets are used as-is in world space.
      - Fixed position targets are used as-is in world space.
      - Direction targets such as UP / DOWN / LEFT / RIGHT use
        base_position + clamped offset.
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
        return _as_xyz(position)

    if isinstance(spec, dict):
        if "node" in spec:
            if cmds_module is None:
                cmds_module = _cmds()
            node = _find_transform_node(str(spec["node"]), cmds_module)
            position = list(cmds_module.xform(node, query=True, worldSpace=True, translation=True))
            return _as_xyz(position)

        if "position" in spec:
            return _as_xyz(spec["position"])

        if "offset" in spec:
            offset = clamp_position(spec["offset"], direction_offset_bounds)
            return resolve_offset_position(base_position, offset)

    return _as_xyz(spec)


def _key_position(node: str, frame: float, xyz: list[float]) -> None:
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
    """Load a `maya_gaze` YAML config and attach path context.

    A single Maya config may contain a shared `maya_common` section plus
    `maya_gaze`, `maya_eye_performance`, and `maya_jali_annotation` sections.
    """
    config_path = Path(path)
    data = _load_yaml_file(config_path)

    common = data.get("maya_common", {}) if isinstance(data, dict) else {}
    config = data.get("maya_gaze", data)
    if not isinstance(common, dict) or not isinstance(config, dict):
        raise ValueError(f"Invalid Maya gaze config: {path}")

    out = {**common, **config}
    clip_name = str(out.get("clip_name", "")).strip()
    if "gaze_events_path" not in out and clip_name:
        out["gaze_events_path"] = f"data/processed/gaze_script/{clip_name}__gaze_events_resolved.json"

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


def _clear_translate_keys(node: str) -> None:
    cmds = _cmds()
    try:
        cmds.cutKey(node, attribute=["translateX", "translateY", "translateZ"], clear=True)
        print(f"[INFO] Cleared existing translate keys on {node}")
    except Exception as exc:
        print(f"[WARN] Failed to clear existing translate keys on {node}: {exc}")


def _apply_flat_weighted_tangents(node: str) -> None:
    """Match the manual Maya commands:
        keyTangent -edit -weightedTangents true;
        keyTangent -itt flat -ott flat;
    """
    cmds = _cmds()
    for attr in ("translateX", "translateY", "translateZ"):
        try:
            cmds.keyTangent(node, edit=True, attribute=attr, weightedTangents=True)
        except Exception:
            pass
        try:
            cmds.keyTangent(node, edit=True, attribute=attr, inTangentType="flat", outTangentType="flat")
        except Exception:
            pass
    print("[INFO] Applied weighted flat tangents to eyeStare translate keys.")


def _find_jsync_node(node_name: str) -> str:
    cmds = _cmds()
    if cmds.objExists(node_name):
        return node_name
    return find_node_by_suffix(node_name)


def apply_jali_attribute_overrides(overrides: dict[str, Any] | None = None) -> None:
    """Apply simple jSync setAttr overrides before writing the gaze overlay."""
    if not overrides:
        return

    cmds = _cmds()
    for node_name, attrs in overrides.items():
        node = _find_jsync_node(str(node_name))
        if not isinstance(attrs, dict):
            raise ValueError(f"JALI override for {node_name!r} must be a mapping of attributes.")
        for attr, value in attrs.items():
            plug = f"{node}.{attr}"
            if not cmds.objExists(plug):
                raise RuntimeError(f"JALI attribute does not exist: {plug}")
            try:
                cmds.setAttr(plug, value)
            except TypeError:
                cmds.setAttr(plug, value, type="string")
            print(f"[INFO] Set {plug} = {value!r}")


def _event_frame_range(event: dict[str, Any], fps: float) -> tuple[int, int]:
    resolved = event["resolved_time"]
    start_frame = sec_to_frame(float(resolved["start"]), fps)
    end_frame = max(start_frame + 1, sec_to_frame(float(resolved["end"]), fps))
    return start_frame, end_frame


def _preflight_resolve_targets(
    events: list[dict[str, Any]],
    target_map: dict[str, Any],
    base_position: Sequence[float],
    direction_offsets: dict[str, Any] | None,
    target_aliases: dict[str, str] | None,
    direction_offset_bounds: dict[str, Any] | None,
    cmds_module: Any,
) -> dict[str, list[float]]:
    """Resolve every target before writing any keyframes."""
    positions: dict[str, list[float]] = {}
    for event in events:
        target = str(event.get("target", ""))
        target_key = resolve_target_alias(target, target_aliases)
        if target_key in positions:
            continue
        positions[target_key] = resolve_target_position(
            target=target,
            target_map=target_map,
            base_position=base_position,
            direction_offsets=direction_offsets,
            target_aliases=target_aliases,
            direction_offset_bounds=direction_offset_bounds,
            cmds_module=cmds_module,
        )
    return positions


def _safe_arrival_frame(start_frame: int, end_frame: float, transition_frames: int) -> float:
    if end_frame <= start_frame:
        return end_frame
    return min(float(start_frame + max(1, transition_frames)), float(end_frame))


def apply_gaze_events(
    gaze_events_path: str,
    target_map: dict,
    fps: float,
    direction_offsets: dict[str, Any] | None = None,
    target_aliases: dict[str, str] | None = None,
    direction_offset_bounds: dict[str, Any] | None = None,
    base_position: Sequence[float] | None = None,
    eye_stare_node_suffix: str = "eyeStare_world",
    clip_end_frame: float | None = None,
    clear_existing_eye_stare_translate_keys: bool = False,
    gaze_transition_frames: int = 3,
    glance_transition_frames: int = 3,
    apply_weighted_flat_tangents: bool = True,
) -> None:
    """
    Apply resolved gaze events to eyeStare_world.

    GAZE / AVERT:
        key previous hold at event start, move over 2-3 frames, then hold
        target until the next gaze event.

    GLANCE:
        key previous hold -> transition to glance target -> hold glance target
        -> transition back to previous hold.
    """
    cmds = _cmds()

    events = _load_gaze_events(gaze_events_path)
    if not events:
        raise RuntimeError(f"No resolved gaze events found in: {gaze_events_path}")

    eye_stare = find_node_by_suffix(eye_stare_node_suffix)
    default_position = _as_xyz(base_position) if base_position is not None else get_world_translation(eye_stare)
    current_hold_position = list(default_position)

    final_hold_frame = float(clip_end_frame) if clip_end_frame is not None else None

    print(f"[INFO] Applying {len(events)} gaze events to {eye_stare}")
    print(f"[INFO] Base eyeStare position: {default_position}")
    if final_hold_frame is not None:
        print(f"[INFO] Final gaze hold frame: {final_hold_frame}")

    target_positions = _preflight_resolve_targets(
        events=events,
        target_map=target_map,
        base_position=default_position,
        direction_offsets=direction_offsets,
        target_aliases=target_aliases,
        direction_offset_bounds=direction_offset_bounds,
        cmds_module=cmds,
    )

    if clear_existing_eye_stare_translate_keys:
        _clear_translate_keys(eye_stare)

    for idx, event in enumerate(events):
        mode = str(event.get("mode", "")).upper()
        target = str(event.get("target", ""))
        target_key = resolve_target_alias(target, target_aliases)
        target_position = target_positions[target_key]

        start_frame, raw_end_frame = _event_frame_range(event, fps)
        end_frame: float = float(raw_end_frame)

        if idx == len(events) - 1 and final_hold_frame is not None:
            end_frame = max(end_frame, final_hold_frame)

        if mode in {"GAZE", "AVERT"}:
            arrival_frame = _safe_arrival_frame(start_frame, end_frame, gaze_transition_frames)

            _key_position(eye_stare, start_frame, current_hold_position)
            _key_position(eye_stare, arrival_frame, target_position)
            _key_position(eye_stare, end_frame, target_position)
            previous_hold = current_hold_position
            current_hold_position = target_position

            print(
                f"[GAZE] {event.get('id')} {mode}-{target}->{target_key}: "
                f"{start_frame}->{arrival_frame}->{end_frame}, "
                f"from={previous_hold}, pos={target_position}"
            )

        elif mode == "GLANCE":
            transition = max(1, int(glance_transition_frames))
            out_arrive_frame = min(float(start_frame + transition), float(end_frame))
            back_start_frame = max(out_arrive_frame, float(end_frame - transition))

            _key_position(eye_stare, start_frame, current_hold_position)
            _key_position(eye_stare, out_arrive_frame, target_position)
            _key_position(eye_stare, back_start_frame, target_position)
            _key_position(eye_stare, end_frame, current_hold_position)

            print(
                f"[GLANCE] {event.get('id')} {mode}-{target}->{target_key}: "
                f"{start_frame}->{out_arrive_frame}->{back_start_frame}->{end_frame}, "
                f"out={target_position}, back={current_hold_position}"
            )

        else:
            print(f"[WARN] Unsupported gaze mode {mode!r}; skipped event {event.get('id')}")

    if apply_weighted_flat_tangents:
        _apply_flat_weighted_tangents(eye_stare)

    print("[DONE] Gaze overlay applied.")


def apply_gaze_events_from_config(config_path: str | Path) -> None:
    config = load_maya_gaze_config(config_path)
    gaze_events_path = resolve_repo_path(config["gaze_events_path"], config)

    apply_jali_attribute_overrides(config.get("jali_attribute_overrides", {}))

    direction_offset_bounds = config.get(
        "direction_offset_bounds",
        config.get("safe_bounds", {}),
    )

    apply_gaze_events(
        gaze_events_path=gaze_events_path,
        target_map=config.get("targets", {}),
        fps=float(config.get("fps", 24.0)),
        direction_offsets=config.get("direction_offsets", {}),
        target_aliases=config.get("target_aliases", {}),
        direction_offset_bounds=direction_offset_bounds,
        base_position=config.get("base_position"),
        eye_stare_node_suffix=str(config.get("eye_stare_node_suffix", "eyeStare_world")),
        clip_end_frame=config.get("clip_end_frame"),
        clear_existing_eye_stare_translate_keys=bool(
            config.get("clear_existing_eye_stare_translate_keys", False)
        ),
        gaze_transition_frames=int(config.get("gaze_transition_frames", 3)),
        glance_transition_frames=int(config.get("glance_transition_frames", 3)),
        apply_weighted_flat_tangents=bool(config.get("apply_weighted_flat_tangents", True)),
    )
