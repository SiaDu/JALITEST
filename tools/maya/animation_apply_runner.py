"""Maya-side application of explicit HCI animation artifact paths."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAYA_CONFIG = REPO_ROOT / "configs" / "maya" / "valleygirl.yaml"

_FPS_BY_UNIT = {
    "game": 15.0,
    "film": 24.0,
    "pal": 25.0,
    "ntsc": 30.0,
    "show": 48.0,
    "palf": 50.0,
    "ntscf": 60.0,
}


def scene_fps_from_unit(unit: str) -> float:
    clean = str(unit).strip().lower()
    if clean in _FPS_BY_UNIT:
        return _FPS_BY_UNIT[clean]
    match = re.fullmatch(r"(\d+(?:\.\d+)?)fps", clean)
    if match and float(match.group(1)) > 0:
        return float(match.group(1))
    raise ValueError(f"Unsupported Maya time unit: {unit!r}")


def current_scene_fps() -> float:
    from maya import cmds  # type: ignore

    return scene_fps_from_unit(str(cmds.currentUnit(query=True, time=True)))


def build_explicit_target_map(
    mappings: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    target_map: dict[str, dict[str, Any]] = {}
    for index, mapping in enumerate(mappings, start=1):
        semantic = str(mapping.get("semantic_target") or "").strip().upper()
        maya_node = str(mapping.get("maya_node") or "").strip()
        if not semantic and not maya_node:
            continue
        if semantic and not maya_node:
            # An unfilled optional UI row is not a mapping. If a compiled gaze
            # actually needs it, gaze preflight reports the missing target.
            continue
        if not semantic:
            raise ValueError(
                f"Look-at mapping row {index} requires both a semantic target and Maya node."
            )
        if semantic in target_map and target_map[semantic]["node"] != maya_node:
            raise ValueError(f"Duplicate look-at target mapping for {semantic!r}.")
        target_map[semantic] = {"node": maya_node}
    # Canonical gaze ends return to the current eyeStare base position.
    target_map["__BASE__"] = {"offset": [0.0, 0.0, 0.0]}
    return target_map


def semantic_target_name(target: str) -> str:
    """Map a canonical typed gaze target to its author-facing mapping name."""
    clean = str(target or "").strip().upper()
    for prefix in ("CHARACTER_", "OBJECT_", "PROP_", "PERSON_"):
        if clean.startswith(prefix):
            return clean[len(prefix):]
    return clean


def validate_gaze_target_mappings(
    gaze_events: Iterable[dict[str, Any]],
    *,
    target_map: dict[str, dict[str, Any]],
    configured_directions: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Preflight semantic gaze targets against Maya/session mappings.

    A session row such as ``HAWK -> |hawk_LOC`` satisfies canonical
    ``OBJECT_HAWK``. The returned map includes that canonical alias for the
    Maya gaze applier while preserving the user's semantic mapping key.
    """
    directions = {str(target).upper() for target in configured_directions}
    resolved_map = dict(target_map)
    missing: set[str] = set()
    for event in gaze_events:
        if not event.get("resolved_time"):
            continue
        target = str(event.get("target") or "").strip().upper()
        if not target or target in directions or target == "__BASE__":
            continue
        semantic = semantic_target_name(target)
        spec = target_map.get(target) or target_map.get(semantic)
        if spec is None:
            missing.add(semantic)
        else:
            resolved_map[target] = spec
    if missing:
        names = sorted(missing)
        if len(names) == 1:
            raise ValueError(f"Missing Maya look-at mapping for semantic target {names[0]}.")
        raise ValueError(
            "Missing Maya look-at mappings for semantic targets: " + ", ".join(names)
        )
    return resolved_map


def _rig_namespace(active_node: str) -> str:
    leaf = str(active_node).strip().rsplit("|", 1)[-1]
    return leaf.rsplit(":", 1)[0] if ":" in leaf else ""


def qualify_rig_control(active_node: str, configured_name: str) -> str:
    name = str(configured_name).strip()
    namespace = _rig_namespace(active_node)
    if not namespace or ":" in name:
        return name
    return f"{namespace}:{name}"


def resolve_jsync_for_character(
    character_node: str, expected_sound_file: str | None = None, *, cmds_module: Any | None = None
) -> str:
    """Resolve the full DAG path of the jSync belonging to one character rig."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    root = str(character_node).rstrip("|")
    candidates = [
        str(node) for node in (cmds_module.ls(type="jSync", long=True) or [])
        if str(node).startswith(root + "|")
    ]
    if expected_sound_file and len(candidates) > 1:
        expected = str(expected_sound_file)
        candidates = [
            node for node in candidates
            if cmds_module.getAttr(f"{node}.sound_file") == expected
        ]
    if not candidates:
        qualifier = f' with sound_file {expected_sound_file!r}' if expected_sound_file else ""
        raise RuntimeError(f"No jSync node found beneath character {character_node!r}{qualifier}.")
    if len(candidates) != 1:
        raise RuntimeError(
            f"Ambiguous jSync nodes beneath character {character_node!r}: {', '.join(candidates)}"
        )
    return candidates[0]


def load_animation_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "hci_animation_manifest_v0":
        raise ValueError(f"Invalid HCI animation manifest: {manifest_path}")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("Animation manifest requires an artifacts object.")
    required = {
        "annotated_for_jali",
        "gaze_events",
        "eye_performance_events",
        "head_events",
        "runtime_transcript",
    }
    missing = sorted(required - set(artifacts))
    if missing:
        raise ValueError(f"Animation manifest is missing artifacts: {missing}")
    for label in required:
        artifact = Path(str(artifacts[label]))
        if not artifact.is_file():
            raise FileNotFoundError(f"Animation artifact {label!r} not found: {artifact}")
    return value


def load_dual_animation_manifest(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "dual_animation_manifest_v0":
        raise ValueError(f"Invalid dual animation manifest: {path}")
    mapping, artifacts = value.get("character_runtime_mapping"), value.get("artifacts")
    if not isinstance(mapping, dict) or not isinstance(artifacts, dict):
        raise ValueError("Dual animation manifest requires character_runtime_mapping and artifacts.")
    for alias in ("A", "B"):
        if not isinstance(mapping.get(alias), dict) or not str(mapping[alias].get("sound_file") or ""):
            raise ValueError(f"Dual animation manifest requires {alias} runtime mapping.")
        artifact_path = Path(str(artifacts.get(alias) or ""))
        if not artifact_path.is_absolute():
            artifact_path = REPO_ROOT / artifact_path
        if not artifact_path.is_file():
            raise FileNotFoundError(f"Dual semantic artifact for {alias} is missing: {artifact_path}")
        artifacts[alias] = str(artifact_path)
    timing_path = Path(str(artifacts.get("conversation_anchor_timing") or ""))
    if timing_path and not timing_path.is_absolute():
        artifacts["conversation_anchor_timing"] = str(REPO_ROOT / timing_path)
    return value


def resolve_character_look_at_target(alias: str, character_mappings: dict[str, dict[str, Any]], *, configured_suffix: str | None = None) -> str:
    row = character_mappings.get(alias) or {}
    explicit = str(row.get("look_at_node") or "").strip()
    if explicit:
        return explicit
    root = str(row.get("maya_node") or "").strip()
    if configured_suffix and root:
        return qualify_rig_control(root, configured_suffix)
    raise ValueError(f"Character {alias} requires an explicit look_at_node (JALI_GRP is not a gaze target).")


def adapt_dual_gaze_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert generic dual gaze rows to Maya gaze rows in chronological order."""
    adapted=[]
    for event in events:
        if event.get("channel") != "gaze" or not event.get("resolved_time"): continue
        mode, _, target = str(event.get("value") or "").partition("-")
        if not target: continue
        # Social AVERT deliberately returns to base rather than looking at the
        # other character. Explicit AVERT-DOWN/etc retain direction targets.
        resolved_target = "__BASE__" if mode == "AVERT" and target in {"A", "B"} else target
        adapted.append({"id": event.get("id") or event.get("phrase_id"), "phrase_id": event.get("phrase_id"), "source_proposal_id": event.get("source_proposal_id"), "reason": event.get("reason"), "type":"gaze", "mode":mode, "target":resolved_target, "social_avert": mode == "AVERT" and target in {"A","B"}, "resolved_time":dict(event["resolved_time"])})
    return sorted(adapted, key=lambda e:(float(e["resolved_time"]["start"]),float(e["resolved_time"]["end"])))


def capture_character_gaze_reference(character_node: str, *, cmds_module: Any | None = None) -> dict[str, Any]:
    """Capture artist-authored neutral gaze; never assume world origin."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    eye_stare = qualify_rig_control(character_node, "eyeStare_world")
    both_eyes = qualify_rig_control(character_node, "CNT_BOTH_EYES")
    if not cmds_module.objExists(eye_stare) or not cmds_module.objExists(both_eyes):
        raise RuntimeError(f"Could not resolve eyeStare_world/CNT_BOTH_EYES for {character_node}")
    return {"eye_stare_node": eye_stare, "eye_stare_world_position": list(cmds_module.xform(eye_stare, query=True, worldSpace=True, translation=True)), "both_eyes_node": both_eyes, "both_eyes_translate": [float(cmds_module.getAttr(f"{both_eyes}.translateX")), float(cmds_module.getAttr(f"{both_eyes}.translateY"))]}


def capture_current_look_at_position(character_node: str, *, cmds_module: Any | None = None) -> list[float]:
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    node=qualify_rig_control(character_node,"eyeStare_world")
    if not cmds_module.objExists(node): raise RuntimeError(f"Could not resolve eyeStare_world for {character_node}")
    return list(cmds_module.xform(node,query=True,worldSpace=True,translation=True))


def resolve_actor_target_position(alias: str, target: str, character_mappings: dict[str, dict[str, Any]]) -> list[float]:
    targets = (character_mappings.get(alias) or {}).get("gaze_targets") or {}
    value = targets.get(target)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"Character {alias} requires an artist-captured gaze target position for {target}.")
    return [float(item) for item in value]


def directional_eye_offset(target: str, *, magnitude: float = 5.0, limit: float = 6.0, social: bool = False) -> tuple[float, float]:
    """Configurable local pupil offset; social AVERT defaults down/away."""
    amount = min(abs(float(magnitude)), abs(float(limit)))
    token = "DOWN" if social else str(target).upper()
    x = -amount if "LEFT" in token else amount if "RIGHT" in token else 0.0
    y = -amount if "DOWN" in token else amount if "UP" in token else 0.0
    return max(-abs(limit), min(abs(limit), x)), max(-abs(limit), min(abs(limit), y))


def clear_character_gaze_animation(reference: dict[str, Any], *, cmds_module: Any | None = None) -> None:
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    for node, attributes in ((reference["eye_stare_node"], ("translateX", "translateY", "translateZ")), (reference["both_eyes_node"], ("translateX", "translateY"))):
        for attribute in attributes:
            cmds_module.cutKey(node, attribute=attribute, clear=True)


def build_dual_gaze_schedule(events: Iterable[dict[str, Any]], *, neutral_position: list[float], neutral_eyes: list[float], target_positions: dict[str, list[float]], magnitude: float = 5.0, limit: float = 6.0) -> list[dict[str, Any]]:
    """Return complete two-layer gaze states, ordered by canonical intervals."""
    schedule=[]; previous={"eye_stare":list(neutral_position),"eyes":list(neutral_eyes)}
    ordered=sorted(events, key=lambda e:(float(e["resolved_time"]["start"]),float(e["resolved_time"]["end"])))
    for index,event in enumerate(ordered):
        mode,target=event["mode"],event["target"]; next_start=float(ordered[index+1]["resolved_time"]["start"]) if index+1<len(ordered) else None
        raw_end=float(event["resolved_time"]["end"])
        end=min(raw_end,next_start) if mode=="GLANCE" and next_start is not None else (next_start if next_start is not None else raw_end)
        state={"event":event,"start":float(event["resolved_time"]["start"]),"end":end}
        state["previous_state"]=dict(previous)
        if mode in {"GAZE","GLANCE"}:
            if target not in target_positions: raise ValueError(f"No artist-captured gaze target position for {target}.")
            state.update({"eye_stare":list(target_positions[target]),"eyes":list(neutral_eyes)})
            if mode=="GLANCE": state["return_state"]=dict(previous)
        else:
            x,y=directional_eye_offset(target,magnitude=magnitude,limit=limit,social=bool(event.get("social_avert")))
            state.update({"eye_stare":list(neutral_position),"eyes":[neutral_eyes[0]+x,neutral_eyes[1]+y]})
        if mode != "GLANCE": previous={"eye_stare":list(state["eye_stare"]),"eyes":list(state["eyes"])}
        schedule.append(state)
    return schedule


def build_dual_gaze_key_schedule(schedule: Iterable[dict[str, Any]], *, fps: float, transition_frames: int = 3, glance_min_hold_seconds: float = 0.5, timeline_start: float = 0.0, initialization_epsilon: float = 1e-6) -> list[dict[str, Any]]:
    """Expand complete semantic states into chronological Maya key states."""
    keys=[]
    ordered = list(schedule)
    for index, state in enumerate(ordered):
        event=state["event"]; start=state["start"]*fps; end=state["end"]*fps; previous=state.get("previous_state", state)
        # A canonical persistent state at scene start is initialization, not a
        # transition from an invented neutral state.  GLANCE stays temporary
        # and deliberately retains its existing transition/return behavior.
        if index == 0 and event["mode"] != "GLANCE" and state["start"] <= timeline_start + initialization_epsilon:
            keys.append({"frame": start, "eye_stare": list(state["eye_stare"]), "eyes": list(state["eyes"])})
            continue
        arrival=min(start+transition_frames,end)
        keys.append({"frame":start,"eye_stare":list(previous["eye_stare"]),"eyes":list(previous["eyes"])})
        if event["mode"]=="GLANCE":
            transition = max(1, int(transition_frames))
            minimum_hold = max(1, int(math.ceil(float(glance_min_hold_seconds) * float(fps))))
            out = start + transition
            back = end - transition
            if back - out < minimum_hold:
                raise ValueError(
                    "GLANCE interval is too short for configured transition and minimum hold."
                )
            returned=state["return_state"]; keys.extend(({"frame":out,"eye_stare":list(state["eye_stare"]),"eyes":list(state["eyes"])},{"frame":back,"eye_stare":list(state["eye_stare"]),"eyes":list(state["eyes"])},{"frame":end,"eye_stare":list(returned["eye_stare"]),"eyes":list(returned["eyes"])}))
        else: keys.append({"frame":arrival,"eye_stare":list(state["eye_stare"]),"eyes":list(state["eyes"])})
    # Never allow an older semantic state to key after a newer start.
    return sorted(keys,key=lambda key:key["frame"])


def _dual_eye_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the small eye adapter payload and reject malformed resolved rows."""
    eye: list[dict[str, Any]] = []
    channels = {"lid": "lid_state", "blink": "performative_blink", "blink_suppression": "blink_suppression"}
    for event in events:
        kind = channels.get(str(event.get("channel") or ""))
        if not kind:
            continue
        resolved_time = event.get("resolved_time")
        if not isinstance(resolved_time, dict) or "start" not in resolved_time or "end" not in resolved_time:
            raise ValueError(f"Dual {kind} event is missing resolved_time.")
        eye.append({"type": kind, "value": event.get("value"), "resolved_time": dict(resolved_time)})
    return eye


def _validate_gaze_reference(reference: dict[str, Any], *, cmds_module: Any) -> None:
    for key in ("eye_stare_node", "both_eyes_node"):
        node = str(reference.get(key) or "")
        if not node or not cmds_module.objExists(node):
            raise RuntimeError(f"Prepared neutral gaze reference has no existing {key}.")
    if not isinstance(reference.get("eye_stare_world_position"), (list, tuple)) or len(reference["eye_stare_world_position"]) != 3:
        raise ValueError("Prepared neutral gaze reference requires eye_stare_world_position.")
    if not isinstance(reference.get("both_eyes_translate"), (list, tuple)) or len(reference["both_eyes_translate"]) != 2:
        raise ValueError("Prepared neutral gaze reference requires both_eyes_translate.")


def prepare_dual_animation_overlay(*, manifest_path: str | Path, character_mappings: dict[str, dict[str, Any]], look_at_mappings: Iterable[dict[str, Any]] = (), maya_config_path: str | Path | None = None) -> dict[str, Any]:
    """Validate and capture a dual overlay before native jSync is frozen/deleted.

    This phase is deliberately read-only with respect to Maya animation.  The
    resulting context is the only input required by the post-freeze overlay.
    """
    from maya import cmds  # type: ignore
    manifest = load_dual_animation_manifest(manifest_path)
    runtime = manifest["character_runtime_mapping"]
    config_path = Path(maya_config_path or os.environ.get("JALITEST_MAYA_CONFIG") or DEFAULT_MAYA_CONFIG)
    source_path=str(REPO_ROOT / "src")
    if source_path not in sys.path: sys.path.insert(0, source_path)
    from expregaze_jali.maya_apply_gaze import load_maya_gaze_config  # noqa: PLC0415
    from expregaze_jali.maya_apply_eye_performance import load_maya_eye_config  # noqa: PLC0415
    gaze_config, eye_config = load_maya_gaze_config(config_path), load_maya_eye_config(config_path)
    # Retain validation of supplied generic session rows without using scene
    # geometry for calibrated character gaze positions.
    build_explicit_target_map(look_at_mappings)
    for alias in ("A","B"):
        row=character_mappings.get(alias)
        if not isinstance(row,dict) or not str(row.get("maya_node") or ""):
            raise ValueError(f"Missing Maya character mapping for {alias}.")
        if str(row.get("script_name") or "").strip().upper()!=str(runtime[alias].get("script_name") or "").strip().upper():
            raise ValueError(f"Maya character mapping {alias} does not match manifest runtime mapping.")
    prepared: dict[str, Any] = {"schema_version": "dual_animation_overlay_prepared_v0", "manifest_path": str(manifest_path), "fps": float(manifest["fps"]), "jsync_nodes": {}}
    for alias in ("A","B"):
        row=character_mappings[alias]; node=str(row["maya_node"])
        if not cmds.objExists(node): raise RuntimeError(f"Maya character node does not exist for {alias}: {node}")
        jsync=resolve_jsync_for_character(node, str(runtime[alias]["sound_file"]))
        if cmds.getAttr(f"{jsync}.sound_file") != str(runtime[alias]["sound_file"]):
            raise RuntimeError(f"jSync for {alias} does not have expected sound_file {runtime[alias]['sound_file']!r}.")
        events=json.loads(Path(manifest["artifacts"][alias]).read_text(encoding="utf-8")).get("events",[])
        if not isinstance(events, list):
            raise ValueError(f"Dual semantic artifact for {alias} requires an events list.")
        gaze=adapt_dual_gaze_events(events)
        provided_reference=row.get("gaze_reference") if isinstance(row.get("gaze_reference"),dict) else None
        reference=dict(provided_reference) if provided_reference else capture_character_gaze_reference(node, cmds_module=cmds)
        reference.setdefault("eye_stare_node",qualify_rig_control(node,"eyeStare_world")); reference.setdefault("both_eyes_node",qualify_rig_control(node,"CNT_BOTH_EYES"))
        _validate_gaze_reference(reference, cmds_module=cmds)
        positions: dict[str, list[float]] = {}
        for item in gaze:
            if item["target"] not in {"__BASE__", "DOWN", "UP", "LEFT", "RIGHT", "DOWN_LEFT", "DOWN_RIGHT", "UP_LEFT", "UP_RIGHT"}:
                positions[item["target"]] = resolve_actor_target_position(alias,item["target"],character_mappings)
        schedule=build_dual_gaze_schedule(gaze,neutral_position=reference["eye_stare_world_position"],neutral_eyes=reference["both_eyes_translate"],target_positions=positions,magnitude=float(gaze_config.get("directional_eye_magnitude",5)),limit=float(gaze_config.get("directional_eye_limit",6)))
        key_schedule=build_dual_gaze_key_schedule(schedule,fps=float(manifest["fps"]),transition_frames=int(gaze_config.get("gaze_transition_frames",3)),glance_min_hold_seconds=float(gaze_config.get("glance_min_hold_seconds",0.5)))
        eye = _dual_eye_events(events)
        prepared["jsync_nodes"][alias] = jsync
        prepared[alias] = {"maya_node": node, "jsync_node": jsync, "sound_file": runtime[alias]["sound_file"], "gaze_reference": reference, "gaze_events": gaze, "gaze_schedule": schedule, "gaze_key_schedule": key_schedule, "eye_events": eye, "eyelid_control_suffix": qualify_rig_control(node, str(eye_config.get("eyelid_control_suffix", "LIDS_jSync_plusMinus"))), "eyelid_attr": str(eye_config.get("eyelid_attr", "Down_upLids_jSync")), "affect_event_count_compiled_not_applied": sum(e.get("channel")=="affect" for e in events), "heart_event_count_compiled_not_applied": sum(e.get("channel")=="heart" for e in events), "head_event_count_compiled_not_applied": sum(e.get("channel")=="head" for e in events)}
    return prepared


def apply_dual_animation_artifacts(*, manifest_path: str | Path | None = None, character_mappings: dict[str, dict[str, Any]] | None = None, look_at_mappings: Iterable[dict[str, Any]] = (), maya_config_path: str | Path | None = None, prepared_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply a prepared dual overlay.  A prepared context permits deleted jSync nodes."""
    from maya import cmds  # type: ignore
    if prepared_context is None:
        if manifest_path is None or character_mappings is None:
            raise ValueError("manifest_path and character_mappings are required when no prepared_context is supplied.")
        prepared_context = prepare_dual_animation_overlay(manifest_path=manifest_path, character_mappings=character_mappings, look_at_mappings=look_at_mappings, maya_config_path=maya_config_path)
    if not isinstance(prepared_context, dict) or prepared_context.get("schema_version") != "dual_animation_overlay_prepared_v0":
        raise ValueError("Invalid prepared dual animation overlay context.")
    source_path=str(REPO_ROOT / "src")
    if source_path not in sys.path: sys.path.insert(0, source_path)
    from expregaze_jali.maya_apply_eye_performance import apply_eye_performance_events  # noqa: PLC0415
    fps = float(prepared_context["fps"])
    base_path = Path(str(prepared_context.get("manifest_path") or manifest_path or "")).parent
    result: dict[str, Any] = {"jsync_nodes": dict(prepared_context.get("jsync_nodes") or {})}
    # Context has already validated both actors.  Only now may either actor be mutated.
    for alias in ("A", "B"):
        item = prepared_context.get(alias)
        if not isinstance(item, dict):
            raise ValueError(f"Prepared dual animation overlay is missing {alias}.")
        reference = dict(item["gaze_reference"])
        clear_character_gaze_animation(reference, cmds_module=cmds)
        for state in item["gaze_key_schedule"]:
            frame=state["frame"]; cmds.xform(reference["eye_stare_node"],worldSpace=True,translation=state["eye_stare"]); cmds.setKeyframe(reference["eye_stare_node"],attribute="translate",time=frame)
            cmds.setAttr(f"{reference['both_eyes_node']}.translateX",state["eyes"][0]); cmds.setAttr(f"{reference['both_eyes_node']}.translateY",state["eyes"][1]); cmds.setKeyframe(reference["both_eyes_node"],attribute="translateX",time=frame); cmds.setKeyframe(reference["both_eyes_node"],attribute="translateY",time=frame)
        adapter_dir=base_path / "maya_adapter" / alias; adapter_dir.mkdir(parents=True,exist_ok=True)
        gaze_path=adapter_dir / "gaze_events.json"; eye_path=adapter_dir / "eye_events.json"
        gaze_path.write_text(json.dumps({"events":item["gaze_events"],"schedule":item["gaze_schedule"],"key_schedule":item["gaze_key_schedule"]}),encoding="utf-8"); eye_path.write_text(json.dumps({"events":item["eye_events"]}),encoding="utf-8")
        if item["eye_events"]: apply_eye_performance_events(eye_events_path=str(eye_path), fps=fps, eyelid_control_suffix=item["eyelid_control_suffix"], eyelid_attr=item["eyelid_attr"])
        result[alias] = {key: item[key] for key in ("jsync_node", "sound_file", "gaze_reference", "affect_event_count_compiled_not_applied", "heart_event_count_compiled_not_applied", "head_event_count_compiled_not_applied")}
        result[alias].update({"gaze_event_count":len(item["gaze_events"]), "eye_event_count":len(item["eye_events"]), "warnings":["affect/heart compiled but not yet applied in dual runtime", "head compiled but not yet applied"]})
    return result


def _event_count(path: str | Path, *keys: str) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return sum(len(data.get(key, [])) for key in keys if isinstance(data.get(key), list))


def apply_animation_artifacts(
    *,
    manifest_path: str | Path,
    active_character_node: str,
    look_at_mappings: Iterable[dict[str, Any]],
    maya_config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply compiled artifacts in Maya without any sequence configuration."""
    from maya import cmds  # type: ignore

    character_node = str(active_character_node).strip()
    if not character_node:
        raise ValueError("Active character Maya node is required.")
    if not cmds.objExists(character_node):
        raise RuntimeError(f"Active character Maya node does not exist: {character_node}")

    source_path = str(REPO_ROOT / "src")
    if source_path not in sys.path:
        sys.path.insert(0, source_path)
    from expregaze_jali.maya_apply_eye_performance import (  # noqa: PLC0415
        apply_eye_performance_events,
        load_maya_eye_config,
    )
    from expregaze_jali.maya_apply_gaze import (  # noqa: PLC0415
        apply_gaze_events,
        load_maya_gaze_config,
    )
    from expregaze_jali.maya_apply_jali_annotation import (  # noqa: PLC0415
        apply_jali_annotation,
        load_jali_annotation_config,
    )

    manifest = load_animation_manifest(manifest_path)
    artifacts = manifest["artifacts"]
    fps = float(manifest["fps"])
    clip_end_frame = float(manifest["clip_end_frame"])
    config_path = Path(
        maya_config_path
        or os.environ.get("JALITEST_MAYA_CONFIG")
        or DEFAULT_MAYA_CONFIG
    )
    if not config_path.is_file():
        raise FileNotFoundError(f"Maya rig configuration not found: {config_path}")

    jali_config = load_jali_annotation_config(config_path)
    gaze_config = load_maya_gaze_config(config_path)
    eye_config = load_maya_eye_config(config_path)
    target_map = build_explicit_target_map(look_at_mappings)
    for target, spec in target_map.items():
        node = spec.get("node")
        if node and not cmds.objExists(node):
            raise RuntimeError(f"Look-at target Maya node does not exist for {target!r}: {node}")

    gaze_data = json.loads(Path(artifacts["gaze_events"]).read_text(encoding="utf-8"))
    configured_directions = {
        str(target).upper() for target in gaze_config.get("direction_offsets", {})
    }
    target_map = validate_gaze_target_mappings(
        gaze_data.get("events", []),
        target_map=target_map,
        configured_directions=configured_directions,
    )

    jsync_node = resolve_jsync_for_character(
        character_node, jali_config.get("expected_sound_file")
    )
    apply_jali_annotation(
        annotated_for_jali_path=artifacts["annotated_for_jali"],
        jali_transcript_path=artifacts["runtime_transcript"],
        jsync_node=jsync_node,
        backup_original_transcript=False,
        trigger_jsync_compute=bool(jali_config.get("trigger_jsync_compute", True)),
        jali_attribute_overrides=jali_config.get("jali_attribute_overrides", {}),
    )

    gaze_count = len(gaze_data.get("events", []))
    if gaze_count:
        apply_gaze_events(
            gaze_events_path=artifacts["gaze_events"],
            target_map=target_map,
            fps=fps,
            direction_offsets=gaze_config.get("direction_offsets", {}),
            target_aliases={},
            direction_offset_bounds=gaze_config.get(
                "direction_offset_bounds", gaze_config.get("safe_bounds", {})
            ),
            base_position=gaze_config.get("base_position"),
            eye_stare_node_suffix=qualify_rig_control(
                character_node,
                str(gaze_config.get("eye_stare_node_suffix", "eyeStare_world")),
            ),
            clip_end_frame=clip_end_frame,
            clear_existing_eye_stare_translate_keys=bool(
                gaze_config.get("clear_existing_eye_stare_translate_keys", False)
            ),
            gaze_transition_frames=int(gaze_config.get("gaze_transition_frames", 3)),
            glance_transition_frames=int(gaze_config.get("glance_transition_frames", 3)),
            apply_weighted_flat_tangents=bool(
                gaze_config.get("apply_weighted_flat_tangents", True)
            ),
        )
    else:
        print("[INFO] No canonical gaze events; gaze apply skipped.")

    eye_count = _event_count(
        artifacts["eye_performance_events"],
        "lid_state_events",
        "performative_blink_events",
        "regulatory_blink_events",
    )
    suppression_count = _event_count(
        artifacts["eye_performance_events"], "blink_suppression_events"
    )
    if eye_count:
        apply_eye_performance_events(
            eye_events_path=artifacts["eye_performance_events"],
            fps=fps,
            eyelid_control_suffix=qualify_rig_control(
                character_node,
                str(eye_config.get("eyelid_control_suffix", "LIDS_jSync_plusMinus")),
            ),
            eyelid_attr=str(eye_config.get("eyelid_attr", "Down_upLids_jSync")),
            clip_end_frame=clip_end_frame,
            default_lid_state=float(eye_config.get("default_lid_state", 0.0)),
            clear_existing_eyelid_keys=bool(
                eye_config.get("clear_existing_eyelid_keys", True)
            ),
            lid_state_transition_frames=int(
                eye_config.get("lid_state_transition_frames", 8)
            ),
            apply_weighted_flat_tangents=bool(
                eye_config.get("apply_weighted_flat_tangents", True)
            ),
            blink_presets=eye_config.get("blink_presets", {}),
        )
    else:
        print("[INFO] No canonical lid or explicit blink events; eyelid key apply skipped.")
    if suppression_count:
        print(
            f"[INFO] {suppression_count} canonical blink-suppression interval(s) "
            "were compiled; no regulatory blinks are generated inside this HCI path."
        )

    head_data = json.loads(Path(artifacts["head_events"]).read_text(encoding="utf-8"))
    head_count = len(head_data.get("events", []))
    if head_count:
        print(
            "[WARN] Head involvement events were compiled but not applied: "
            "no Maya head applier exists yet."
        )

    result = {
        "jali_applied": True,
        "gaze_event_count": gaze_count,
        "eye_event_count": eye_count,
        "blink_suppression_interval_count": suppression_count,
        "head_event_count_compiled_not_applied": head_count,
        "active_character_node": character_node,
    }
    print(f"[DONE] HCI animation artifacts applied: {result}")
    return result
