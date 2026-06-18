from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from expregaze_jali.maya_control_utils import find_node_by_suffix, sec_to_frame


def _cmds():
    try:
        import maya.cmds as cmds  # type: ignore
        return cmds
    except Exception as exc:
        raise RuntimeError(
            "maya_apply_eye_performance must be run inside Autodesk Maya's Python environment."
        ) from exc


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


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence_overrides(sequence_config_path: str | Path | None) -> dict[str, Any]:
    if not sequence_config_path:
        return {}

    data = _load_yaml_file(Path(sequence_config_path))
    sequence = _mapping(data.get("sequence", data))
    jali = _mapping(data.get("jali"))

    sequence_id = sequence.get("sequence_id") or jali.get("clip_name")
    clip_name = jali.get("clip_name") or sequence_id

    out: dict[str, Any] = {
        "_sequence_config_path": str(sequence_config_path),
    }
    if sequence_id:
        out["sequence_id"] = str(sequence_id)
    if clip_name:
        out["clip_name"] = str(clip_name)
    if sequence.get("fps") not in (None, ""):
        out["fps"] = float(sequence["fps"])
    if sequence.get("clip_end_frame") not in (None, ""):
        out["clip_end_frame"] = float(sequence["clip_end_frame"])

    return out


def _compiled_output_dir_from_project(project_config_path: str | Path | None) -> str:
    if not project_config_path:
        return "data/processed/gaze_script"

    path = Path(project_config_path)
    try:
        data = _load_yaml_file(path)
        project_data = _mapping(data.get("data"))
        return str(project_data.get("compiled_output_dir") or "data/processed/gaze_script")
    except Exception:
        # The small Maya YAML loader only supports a subset. For project.yaml we
        # only need this scalar, so fall back to scanning the text.
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("compiled_output_dir:"):
                value = line.split(":", 1)[1].strip().strip("\"'")
                return value or "data/processed/gaze_script"
        return "data/processed/gaze_script"


def load_maya_eye_config(
    path: str | Path,
    *,
    sequence_config_path: str | Path | None = None,
    project_config_path: str | Path | None = None,
) -> dict[str, Any]:
    config_path = Path(path)
    data = _load_yaml_file(config_path)
    common = data.get("maya_common", {}) if isinstance(data, dict) else {}
    config = data.get("maya_eye_performance", data)
    if not isinstance(common, dict) or not isinstance(config, dict):
        raise ValueError(f"Invalid Maya eye performance config: {path}")

    out = {**common, **config}
    out.update(_sequence_overrides(sequence_config_path))

    clip_name = str(out.get("clip_name", "")).strip()
    if "eye_events_path" not in out and clip_name:
        compiled_output_dir = _compiled_output_dir_from_project(project_config_path)
        out["eye_events_path"] = f"{compiled_output_dir}/{clip_name}__actor_overlay_events.json"

    out["_config_path"] = str(config_path)
    if project_config_path:
        out["_project_config_path"] = str(project_config_path)

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
    path = Path(path_value)
    if path.is_absolute():
        return str(path)
    project_root = Path(config.get("_project_root", "."))
    return str(project_root / path)


def _find_jsync_node(node_name: str) -> str:
    cmds = _cmds()
    if cmds.objExists(node_name):
        return node_name
    return find_node_by_suffix(node_name)


def apply_jali_attribute_overrides(overrides: dict[str, Any] | None = None) -> None:
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
                print(f"[WARN] JALI attribute does not exist, skipped: {plug}")
                continue
            try:
                cmds.setAttr(plug, value)
            except TypeError:
                cmds.setAttr(plug, value, type="string")
            print(f"[INFO] Set {plug} = {value!r}")


def _load_eye_events(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    # Current Step 03 writes actor overlay events as:
    #   {clip}__actor_overlay_events.json
    # with one flat `events` list containing lid_state / performative_blink /
    # blink_suppression entries. The Maya eyelid adapter still consumes the older
    # grouped shape, so normalize it here.
    if isinstance(data, dict) and "events" in data and (
        "lid_state_events" not in data
        and "performative_blink_events" not in data
        and "regulatory_blink_events" not in data
    ):
        lid_state_events: list[dict[str, Any]] = []
        performative_blink_events: list[dict[str, Any]] = []
        blink_suppression_events: list[dict[str, Any]] = []

        for event in data.get("events", []):
            if not isinstance(event, dict):
                continue

            event_type = str(event.get("type", "")).strip()
            normalized = dict(event)

            if event_type == "lid_state":
                lid_state_events.append(normalized)

            elif event_type == "performative_blink":
                # `value` stores the blink preset name, e.g. DOUBLE_BLINK or
                # EYE_CLOSE_HOLD. The Maya blink applier expects `mode`.
                normalized.setdefault("mode", str(normalized.get("value") or "SINGLE_BLINK").upper())
                normalized.setdefault("class", "performative")
                performative_blink_events.append(normalized)

            elif event_type == "blink_suppression":
                blink_suppression_events.append(normalized)

        return {
            "clip_name": data.get("clip_name", ""),
            "lid_state_events": lid_state_events,
            "performative_blink_events": performative_blink_events,
            "regulatory_blink_events": [],
            "blink_suppression_events": blink_suppression_events,
            "diagnostics": data.get("diagnostics", {}),
        }

    return data

def _plug(node: str, attr: str) -> str:
    return f"{node}.{attr}"


def _key_attr(node: str, attr: str, frame: float, value: float) -> None:
    cmds = _cmds()
    cmds.setAttr(_plug(node, attr), float(value))
    cmds.setKeyframe(node, attribute=attr, time=float(frame))


def _clear_attr_keys(node: str, attr: str) -> None:
    cmds = _cmds()
    try:
        cmds.cutKey(node, attribute=attr, clear=True)
        print(f"[INFO] Cleared existing keys on {node}.{attr}")
    except Exception as exc:
        print(f"[WARN] Failed to clear keys on {node}.{attr}: {exc}")


def _apply_flat_weighted_tangents(node: str, attr: str) -> None:
    cmds = _cmds()
    try:
        cmds.keyTangent(node, edit=True, attribute=attr, weightedTangents=True)
    except Exception:
        pass
    try:
        cmds.keyTangent(node, edit=True, attribute=attr, inTangentType="flat", outTangentType="flat")
    except Exception:
        pass
    print(f"[INFO] Applied weighted flat tangents to {node}.{attr}")


def _event_start_frame(event: dict[str, Any], fps: float) -> int | None:
    rt = event.get("resolved_time")
    if not rt:
        return None
    return sec_to_frame(float(rt["start"]), fps)


def _event_end_frame(event: dict[str, Any], fps: float) -> int | None:
    rt = event.get("resolved_time")
    if not rt:
        return None
    return sec_to_frame(float(rt["end"]), fps)


def _blink_time_frame(event: dict[str, Any], fps: float) -> int | None:
    if event.get("time") is not None:
        return sec_to_frame(float(event["time"]), fps)
    return _event_start_frame(event, fps)


def _build_lid_schedule(
    lid_state_events: list[dict[str, Any]],
    fps: float,
    clip_end_frame: float | None,
    default_value: float,
) -> list[dict[str, Any]]:
    schedule = []
    events = sorted(
        [event for event in lid_state_events if event.get("resolved_time")],
        key=lambda event: float(event["resolved_time"]["start"]),
    )

    for idx, event in enumerate(events):
        start = _event_start_frame(event, fps)
        end = _event_end_frame(event, fps)
        if start is None:
            continue
        if idx + 1 < len(events):
            next_start = _event_start_frame(events[idx + 1], fps)
            if next_start is not None:
                end = next_start
        elif clip_end_frame is not None:
            end = int(round(float(clip_end_frame)))
        if end is None or end <= start:
            end = start + 1

        schedule.append(
            {
                "id": event.get("id"),
                "start_frame": start,
                "end_frame": end,
                "value": float(event.get("value", default_value)),
                "reason": event.get("reason", ""),
            }
        )

    return schedule


def _lid_value_at_frame(schedule: list[dict[str, Any]], frame: float, default_value: float) -> float:
    value = float(default_value)
    for item in schedule:
        if float(item["start_frame"]) <= float(frame):
            value = float(item["value"])
        else:
            break
    return value


def _apply_lid_states(
    node: str,
    attr: str,
    schedule: list[dict[str, Any]],
    default_value: float,
    transition_frames: int,
) -> None:
    current = float(default_value)
    for item in schedule:
        start = float(item["start_frame"])
        end = float(item["end_frame"])
        value = float(item["value"])
        arrive = min(start + max(1, int(transition_frames)), end)

        _key_attr(node, attr, start, current)
        _key_attr(node, attr, arrive, value)
        _key_attr(node, attr, end, value)

        print(
            f"[LID] {item.get('id')} {start}->{arrive}->{end}, "
            f"{current} -> {value}, reason={item.get('reason', '')}"
        )
        current = value


def _blink_pattern(
    mode: str,
    start_frame: int,
    baseline: float,
    presets: dict[str, Any],
) -> list[tuple[float, float]]:
    mode_key = str(mode or "SINGLE_BLINK").upper()
    spec = presets.get(mode_key, presets.get("SINGLE_BLINK", {}))

    closure = float(spec.get("closure", 8))
    close_frames = int(spec.get("close_frames", 2))
    hold_frames = int(spec.get("hold_frames", 1))
    open_frames = int(spec.get("open_frames", 2))
    gap_frames = int(spec.get("gap_frames", 4))
    count = int(spec.get("count", 1))

    keys: list[tuple[float, float]] = []
    cursor = float(start_frame)

    for _ in range(max(1, count)):
        keys.append((cursor, baseline))
        keys.append((cursor + close_frames, closure))
        keys.append((cursor + close_frames + hold_frames, closure))
        keys.append((cursor + close_frames + hold_frames + open_frames, baseline))
        cursor = cursor + close_frames + hold_frames + open_frames + gap_frames

    return keys


def _apply_blinks(
    node: str,
    attr: str,
    blink_events: list[dict[str, Any]],
    schedule: list[dict[str, Any]],
    fps: float,
    default_value: float,
    presets: dict[str, Any],
) -> None:
    for event in sorted(blink_events, key=lambda item: float(item.get("time") or 0.0)):
        frame = _blink_time_frame(event, fps)
        if frame is None:
            continue

        baseline = _lid_value_at_frame(schedule, frame, default_value)
        mode = str(event.get("mode", "SINGLE_BLINK")).upper()
        if event.get("closure") is not None or event.get("duration_frames") is not None:
            # Allow regulatory events to override the default single blink preset.
            presets = dict(presets)
            presets[mode] = dict(presets.get(mode, presets.get("SINGLE_BLINK", {})))
            if event.get("closure") is not None:
                presets[mode]["closure"] = float(event["closure"])
            if event.get("duration_frames") is not None:
                duration = max(2, int(event["duration_frames"]))
                presets[mode]["close_frames"] = max(1, duration // 3)
                presets[mode]["hold_frames"] = max(1, duration // 3)
                presets[mode]["open_frames"] = max(1, duration - presets[mode]["close_frames"] - presets[mode]["hold_frames"])

        keys = _blink_pattern(mode, frame, baseline, presets)
        for key_frame, value in keys:
            _key_attr(node, attr, key_frame, value)

        print(
            f"[BLINK] {event.get('id')} {event.get('class')} {mode} "
            f"at frame {frame}, baseline={baseline}, reason={event.get('reason', '')}"
        )


def apply_eye_performance_events(
    eye_events_path: str,
    fps: float,
    eyelid_control_suffix: str,
    eyelid_attr: str,
    clip_end_frame: float | None = None,
    default_lid_state: float = 0.0,
    clear_existing_eyelid_keys: bool = True,
    lid_state_transition_frames: int = 8,
    apply_weighted_flat_tangents: bool = True,
    blink_presets: dict[str, Any] | None = None,
) -> None:
    cmds = _cmds()

    data = _load_eye_events(eye_events_path)
    node = find_node_by_suffix(eyelid_control_suffix)

    if not cmds.objExists(_plug(node, eyelid_attr)):
        raise RuntimeError(f"Eyelid attribute does not exist: {node}.{eyelid_attr}")

    if clear_existing_eyelid_keys:
        _clear_attr_keys(node, eyelid_attr)

    presets = blink_presets or {}

    schedule = _build_lid_schedule(
        lid_state_events=data.get("lid_state_events", []),
        fps=fps,
        clip_end_frame=clip_end_frame,
        default_value=default_lid_state,
    )

    print(f"[INFO] Applying eye performance to {node}.{eyelid_attr}")
    print(f"[INFO] fps={fps}, default_lid_state={default_lid_state}, lid states={len(schedule)}")

    _apply_lid_states(
        node=node,
        attr=eyelid_attr,
        schedule=schedule,
        default_value=default_lid_state,
        transition_frames=lid_state_transition_frames,
    )

    blink_events = list(data.get("regulatory_blink_events", [])) + list(
        data.get("performative_blink_events", [])
    )
    _apply_blinks(
        node=node,
        attr=eyelid_attr,
        blink_events=blink_events,
        schedule=schedule,
        fps=fps,
        default_value=default_lid_state,
        presets=presets,
    )

    if apply_weighted_flat_tangents:
        _apply_flat_weighted_tangents(node, eyelid_attr)

    print("[DONE] Eye performance overlay applied.")


def apply_eye_performance_events_from_config(
    config_path: str | Path,
    *,
    sequence_config_path: str | Path | None = None,
    project_config_path: str | Path | None = None,
) -> None:
    config = load_maya_eye_config(
        config_path,
        sequence_config_path=sequence_config_path,
        project_config_path=project_config_path,
    )

    if "eye_events_path" not in config:
        raise KeyError(
            "eye_events_path is missing. Set JALITEST_SEQUENCE_CONFIG or add "
            "sequence.sequence_id / jali.clip_name to the sequence config."
        )

    eye_events_path = resolve_repo_path(config["eye_events_path"], config)

    print(f"[INFO] Maya eye config: {config_path}")
    if sequence_config_path:
        print(f"[INFO] Sequence config: {sequence_config_path}")
    print(f"[INFO] Clip name: {config.get('clip_name', '')}")
    print(f"[INFO] Eye events path: {eye_events_path}")

    # Preflight before mutating jSync / eyelid keys.
    if not Path(eye_events_path).exists():
        raise FileNotFoundError(
            "Actor overlay events JSON not found: "
            + str(eye_events_path)
            + ". Run Step 03 compile for the same sequence first."
        )

    apply_jali_attribute_overrides(config.get("jali_attribute_overrides", {}))

    apply_eye_performance_events(
        eye_events_path=eye_events_path,
        fps=float(config.get("fps", 30.0)),
        eyelid_control_suffix=str(config.get("eyelid_control_suffix", "LIDS_jSync_plusMinus")),
        eyelid_attr=str(config.get("eyelid_attr", "Down_upLids_jSync")),
        clip_end_frame=config.get("clip_end_frame"),
        default_lid_state=float(config.get("default_lid_state", 0.0)),
        clear_existing_eyelid_keys=bool(config.get("clear_existing_eyelid_keys", True)),
        lid_state_transition_frames=int(config.get("lid_state_transition_frames", 8)),
        apply_weighted_flat_tangents=bool(config.get("apply_weighted_flat_tangents", True)),
        blink_presets=config.get("blink_presets", {}),
    )
