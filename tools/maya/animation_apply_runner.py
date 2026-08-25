"""Maya-side application of explicit HCI animation artifact paths."""

from __future__ import annotations

import json
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
