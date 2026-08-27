"""Maya-side application of explicit HCI animation artifact paths."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Iterable

from listener_mask_library import AU_TO_USER_CONTROL, EYELID_AUS, PROVENANCE, parse_mask_state, unmapped_expressive_eyelid_aus, user_pose_for_mask


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


def resolve_jali_source_transcript_path(text_input_path: str | Path, sound_file: str) -> Path:
    """Resolve a JALI text_input_path without consulting live transcript text."""
    raw = Path(str(text_input_path).strip())
    if not str(raw):
        raise ValueError("JALI text_input_path is empty.")
    path = raw if raw.suffix.lower() == ".txt" else raw / f"{Path(str(sound_file)).name}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"JALI source transcript not found: {path}")
    return path.resolve()


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


def _enum_index(node: str, attr: str, label: str, cmds_module: Any) -> int:
    values = (cmds_module.attributeQuery(attr, node=node, listEnum=True) or [""])[0].split(":")
    for index, value in enumerate(values):
        if value.strip().casefold() == label.casefold(): return index
    raise RuntimeError(f"{node}.{attr} has no enum label {label!r}: {values}")


LISTENER_MASK_LAYER_PREFIX = "JALITEST_listenerMask_"
GAZE_LAYER_PREFIX = "JALITEST_gaze_"
HEAD_LAYER_PREFIX = "JALITEST_head_"
BLINK_LAYER_PREFIX = "JALITEST_blink_"
JALI_BASELINE_SCHEMA = "dual_jali_base_v2"
_JSYNC_BASELINE_ATTRS = (
    "calculate_paralinguals", "paralingual_bearing", "paralingual_intensity",
    "calculate_expression", "expression_source", "expression_strength",
    "override_annotation", "calculate_blinks",
)


def _set_jali_attr(cmds_module: Any, plug: str, value: object) -> None:
    if isinstance(value, str):
        cmds_module.setAttr(plug, value, type="string")
    else:
        cmds_module.setAttr(plug, value)


def capture_dual_jali_base(
    *, character_mappings: dict[str, dict[str, Any]], cmds_module: Any | None = None,
) -> dict[str, Any]:
    """Capture immutable pre-JALITEST state for both live dual jSync rigs."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    baseline: dict[str, Any] = {"schema_version": JALI_BASELINE_SCHEMA, "actors": {}}
    for alias, row in character_mappings.items():
        rig = str(row.get("maya_node") or "").strip()
        script_name = str(row.get("script_name") or "").strip()
        if not rig or not script_name or not cmds_module.objExists(rig):
            raise RuntimeError(f"{alias}: valid mapped JALI_GRP and script_name are required for JALI baseline capture.")
        jsync = resolve_jsync_for_character(rig, cmds_module=cmds_module)
        if not jsync.startswith(rig.rstrip("|") + "|"):
            raise RuntimeError(f"{alias}: resolved jSync does not belong to mapped rig.")
        required = (*_JSYNC_BASELINE_ATTRS, "sound_file", "text_input_path", "sound_input_path", "output_path", "transcript")
        missing = [attr for attr in required if not cmds_module.objExists(f"{jsync}.{attr}")]
        if missing:
            raise RuntimeError(f"{alias}: jSync is missing baseline attributes: {', '.join(missing)}")
        facs = qualify_rig_control(rig, "FACSMaster")
        facs_plug = f"{facs}.FACS_animationSource"
        if not cmds_module.objExists(facs_plug):
            raise RuntimeError(f"{alias}: missing {facs_plug}.")
        values = {attr: cmds_module.getAttr(f"{jsync}.{attr}") for attr in required}
        try:
            gaze_reference = capture_character_gaze_reference(rig, cmds_module=cmds_module)
        except RuntimeError:
            if alias not in {"A", "B"}:
                raise
            gaze_reference = None
        baseline["actors"][alias] = {
            "script_name": script_name, "maya_node": rig, "jsync": jsync,
            "sound_file": values.pop("sound_file"), "text_input_path": values.pop("text_input_path"),
            "sound_input_path": values.pop("sound_input_path"), "output_path": values.pop("output_path"),
            "transcript": values.pop("transcript"), "jsync_attrs": values,
            "facs_animation_source": cmds_module.getAttr(facs_plug),
            "gaze_reference": gaze_reference,
        }
    return baseline


def capture_dual_jali_base_if_absent(
    baseline: dict[str, Any] | None, *, character_mappings: dict[str, dict[str, Any]], cmds_module: Any | None = None,
) -> dict[str, Any]:
    """Keep the first baseline immutable across repeated Generate operations."""
    return baseline if isinstance(baseline, dict) else capture_dual_jali_base(
        character_mappings=character_mappings, cmds_module=cmds_module
    )


def _validate_dual_jali_base(
    baseline: dict[str, Any], character_mappings: dict[str, dict[str, Any]], *, cmds_module: Any, mel_module: Any,
) -> dict[str, dict[str, Any]]:
    if baseline.get("schema_version") != JALI_BASELINE_SCHEMA or not isinstance(baseline.get("actors"), dict):
        raise ValueError("No valid JALI Base baseline is available to restore.")
    if not mel_module.eval('exists "realign_node"'):
        raise RuntimeError("Installed JALI realign_node procedure is unavailable.")
    prepared: dict[str, dict[str, Any]] = {}
    for alias, row in character_mappings.items():
        item = baseline["actors"].get(alias)
        if not isinstance(item, dict):
            raise ValueError(f"{alias}: JALI Base baseline is missing.")
        rig, jsync = str(item.get("maya_node") or ""), str(item.get("jsync") or "")
        if rig != str(row.get("maya_node") or "") or str(item.get("script_name") or "") != str(row.get("script_name") or ""):
            raise RuntimeError(f"{alias}: current mapping does not match the captured JALI Base baseline.")
        if not rig or not cmds_module.objExists(rig):
            raise RuntimeError(f"{alias}: mapped JALI_GRP no longer exists.")
        if not jsync or not cmds_module.objExists(jsync):
            raise RuntimeError("Cannot restore automatically because the original jSync is missing.")
        if not jsync.startswith(rig.rstrip("|") + "|"):
            raise RuntimeError(f"{alias}: baseline jSync does not belong to mapped rig.")
        if cmds_module.getAttr(f"{jsync}.sound_file") != item.get("sound_file"):
            raise RuntimeError(f"{alias}: live jSync sound_file does not match JALI Base baseline.")
        text_input = str(item.get("text_input_path") or "").strip()
        sound = str(item.get("sound_file") or "").strip()
        if text_input and sound:
            resolve_jali_source_transcript_path(text_input, sound)
        facs_plug = f"{qualify_rig_control(rig, 'FACSMaster')}.FACS_animationSource"
        if not cmds_module.objExists(facs_plug):
            raise RuntimeError(f"{alias}: missing {facs_plug}.")
        reference = item.get("gaze_reference")
        if reference is None and alias in {"A", "B"}:
            reference = {}
        if not isinstance(reference, dict):
            raise ValueError(f"{alias}: JALI Base baseline lacks named gaze reference.")
        prepared[alias] = {"baseline": item, "facs_plug": facs_plug, "gaze_reference": reference, "layers": [f"{LISTENER_MASK_LAYER_PREFIX}{alias}", f"{GAZE_LAYER_PREFIX}{alias}", f"{HEAD_LAYER_PREFIX}{alias}", f"{BLINK_LAYER_PREFIX}{alias}"]}
    return prepared


def restore_dual_jali_base(
    *, baseline: dict[str, Any], character_mappings: dict[str, dict[str, Any]], cmds_module: Any | None = None, mel_module: Any | None = None,
) -> dict[str, Any]:
    """Remove JALITEST overlays and regenerate the captured live JALI base."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    if mel_module is None:
        from maya import mel as mel_module  # type: ignore
    prepared = _validate_dual_jali_base(baseline, character_mappings, cmds_module=cmds_module, mel_module=mel_module)
    selection = cmds_module.ls(selection=True, long=True) or []
    removed: list[str] = []
    try:
        for alias in prepared:
            for layer in prepared[alias]["layers"]:
                if cmds_module.objExists(layer):
                    cmds_module.delete(layer); removed.append(layer)
        for alias in prepared:
            item, saved = prepared[alias], prepared[alias]["baseline"]
            jsync = str(saved["jsync"])
            reference = item["gaze_reference"]
            for attr, value in saved["jsync_attrs"].items():
                _set_jali_attr(cmds_module, f"{jsync}.{attr}", value)
            for attr in ("transcript", "text_input_path", "sound_input_path", "output_path"):
                _set_jali_attr(cmds_module, f"{jsync}.{attr}", saved[attr])
            mel_module.eval(f'realign_node "{jsync.rsplit("|", 1)[-1]}"')
            _set_jali_attr(cmds_module, item["facs_plug"], saved["facs_animation_source"])
            # realign_node may recompute these controls.  The captured base neutral
            # is therefore the final operation on the gaze controls.
            if reference:
                for axis, value in zip("XYZ", reference["eye_stare_translate"]):
                    cmds_module.setAttr(f"{reference['eye_stare_node']}.translate{axis}", value)
                cmds_module.setAttr(f"{reference['both_eyes_node']}.translateX", reference["both_eyes_translate"][0])
                cmds_module.setAttr(f"{reference['both_eyes_node']}.translateY", reference["both_eyes_translate"][1])
    finally:
        if selection:
            cmds_module.select(selection, replace=True)
        else:
            cmds_module.select(clear=True)
    for alias in prepared:
        saved, item = prepared[alias]["baseline"], prepared[alias]
        jsync = str(saved["jsync"])
        if not cmds_module.objExists(jsync) or cmds_module.getAttr(f"{jsync}.sound_file") != saved["sound_file"]:
            raise RuntimeError(f"{alias}: JALI Base post-restore jSync validation failed.")
        if cmds_module.getAttr(f"{jsync}.transcript") != saved["transcript"]:
            raise RuntimeError(f"{alias}: JALI Base transcript was not restored.")
        if any(cmds_module.getAttr(f"{jsync}.{attr}") != value for attr, value in saved["jsync_attrs"].items()):
            raise RuntimeError(f"{alias}: JALI Base jSync attributes were not restored.")
        if cmds_module.getAttr(item["facs_plug"]) != saved["facs_animation_source"]:
            raise RuntimeError(f"{alias}: FACSMaster.FACS_animationSource was not restored.")
        reference = item["gaze_reference"]
        if reference:
            expected = [*reference["eye_stare_translate"], *reference["both_eyes_translate"]]
            actual = [
                *(cmds_module.getAttr(f"{reference['eye_stare_node']}.translate{axis}") for axis in "XYZ"),
                cmds_module.getAttr(f"{reference['both_eyes_node']}.translateX"),
                cmds_module.getAttr(f"{reference['both_eyes_node']}.translateY"),
            ]
            if any(abs(float(current) - float(wanted)) > 1e-5 for current, wanted in zip(actual, expected)):
                raise RuntimeError(f"{alias}: JALI Base final gaze neutral validation failed.")
        if any(cmds_module.objExists(layer) for layer in item["layers"]):
            raise RuntimeError(f"{alias}: JALITEST overlay layers remain after restore.")
    return {"restored": {alias: str(prepared[alias]["baseline"]["script_name"]) for alias in prepared}, "removed_layers": removed, "jsync_preserved": True}


def _listener_affect_by_phrase(events: Iterable[dict[str, Any]]) -> dict[str, object]:
    """Read the complete-state affect event for each canonical phrase."""
    values: dict[str, object] = {}
    for event in events:
        if event.get("channel") != "affect":
            continue
        phrase_id = str(event.get("phrase_id") or "").strip()
        if not phrase_id:
            raise ValueError("Listener affect event is missing phrase_id.")
        if phrase_id in values:
            raise ValueError(f"More than one listener affect event exists for {phrase_id}.")
        values[phrase_id] = event.get("value")
    return values


def build_listener_mask_timeline(
    phrase_timing: Iterable[dict[str, Any]], *, events_by_actor: dict[str, Iterable[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    """Resolve complete states to listener-only, canonical-time Mask targets."""
    actors = list(events_by_actor)
    affects = {alias: _listener_affect_by_phrase(events_by_actor.get(alias, ())) for alias in actors}
    result: dict[str, list[dict[str, Any]]] = {alias: [] for alias in actors}
    for phrase in phrase_timing:
        phrase_id, speaker = str(phrase.get("phrase_id") or ""), str(phrase.get("speaker") or "")
        if not phrase_id or speaker not in result:
            raise ValueError("Canonical phrase timing requires phrase_id and a known speaker.")
        try:
            start, end = float(phrase["canonical_start"]), float(phrase["canonical_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Canonical phrase {phrase_id} has invalid timing.") from exc
        if end < start:
            raise ValueError(f"Canonical phrase {phrase_id} ends before it starts.")
        for alias in actors:
            raw_state: object = None if alias == speaker else affects[alias].get(phrase_id)
            # Validate all semantic Mask states even when the actor is speaking;
            # that keeps preflight deterministic across future role changes.
            if alias != speaker:
                name, intensity = parse_mask_state(raw_state)
                state = "NONE" if name == "NONE" else f"{name}-{intensity:g}"
            else:
                state = "NONE"
            result[alias].append({"phrase_id": phrase_id, "speaker": speaker, "start": start, "end": end, "state": state, "pose": user_pose_for_mask(state)})
    return result


def build_listener_mask_key_schedule(
    intervals: Iterable[dict[str, Any]], *, fps: float, transition_frames: int = 4
) -> list[dict[str, Any]]:
    """Emit only complete-state changes, using the frozen +/- half transition."""
    if fps <= 0 or transition_frames < 1:
        raise ValueError("Listener Mask timing requires positive fps and transition_frames.")
    half = transition_frames / 2.0
    keys: list[dict[str, Any]] = []
    previous: dict[str, float] | None = None
    for interval in intervals:
        pose = dict(interval["pose"])
        boundary = float(interval["start"]) * fps
        if previous == pose:
            continue
        if previous is None:
            # Initial state is established at the canonical start, not before
            # scene time zero.
            keys.append({"frame": boundary, "pose": pose, "phrase_id": interval["phrase_id"]})
        else:
            keys.append({"frame": max(0.0, boundary - half), "pose": previous, "phrase_id": interval["phrase_id"]})
            keys.append({"frame": boundary + half, "pose": pose, "phrase_id": interval["phrase_id"]})
        previous = pose
    return keys


def build_v2_listener_mask_key_schedule(
    intervals: Iterable[dict[str, Any]], *, fps: float
) -> list[dict[str, Any]]:
    """Realize exact v2 semantic boundaries with role-aware visual interpolation."""
    if fps <= 0:
        raise ValueError("V2 Listener Mask timing requires positive fps.")
    keys: list[dict[str, Any]] = []
    previous: dict[str, float] | None = None
    for interval in intervals:
        pose = dict(interval["pose"])
        boundary = float(interval["start"]) * fps
        if previous == pose:
            continue
        kind = str(interval.get("boundary_kind") or "INITIAL_STATE")
        role = str(interval.get("timing_role") or "")
        if previous is None or kind == "INITIAL_STATE":
            keys.append({"frame": 0.0, "pose": pose, "phrase_id": interval["phrase_id"]})
        elif kind == "turn_start" or (kind == "affect" and role == "SPEAK_ONSET"):
            keys.append({"frame": max(0.0, boundary - 4.0), "pose": previous, "phrase_id": interval["phrase_id"]})
            keys.append({"frame": boundary, "pose": pose, "phrase_id": interval["phrase_id"]})
        else:
            keys.append({"frame": boundary, "pose": previous, "phrase_id": interval["phrase_id"]})
            keys.append({"frame": boundary + 4.0, "pose": pose, "phrase_id": interval["phrase_id"]})
        previous = pose
    return keys


def _listener_layer_name(alias: str) -> str:
    return f"{LISTENER_MASK_LAYER_PREFIX}{alias}"


def gaze_layer_name(alias: str) -> str:
    return f"{GAZE_LAYER_PREFIX}{alias}"


def prepare_dual_listener_mask_artifacts(
    *, manifest_path: str | Path, character_mappings: dict[str, dict[str, Any]], cmds_module: Any | None = None
) -> dict[str, Any]:
    """Preflight both User FACS lanes before either actor is changed."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    manifest = load_dual_animation_manifest(manifest_path)
    timing_path = Path(str(manifest["artifacts"].get("conversation_phrase_timing") or ""))
    if not timing_path.is_file():
        raise FileNotFoundError("Dual listener Mask requires conversation_phrase_timing.json.")
    phrases = json.loads(timing_path.read_text(encoding="utf-8")).get("phrases")
    if not isinstance(phrases, list):
        raise ValueError("conversation_phrase_timing.json requires a phrases list.")
    events_by_actor: dict[str, list[dict[str, Any]]] = {}
    character_artifacts = manifest["artifacts"].get("characters", {})
    actors = list(manifest.get("characters", ["A", "B"]))
    for alias in actors:
        path = Path(str((character_artifacts.get(alias) or {}).get("semantic_events") or manifest["artifacts"].get(alias) or ""))
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload.get("events"), list):
            raise ValueError(f"Dual semantic artifact for {alias} requires an events list.")
        events_by_actor[alias] = payload["events"]
        for event in events_by_actor[alias]:
            if event.get("channel") == "affect":
                parse_mask_state(event.get("value"))
    timeline = build_listener_mask_timeline(phrases, events_by_actor=events_by_actor)
    prepared: dict[str, Any] = {"schema_version": "dual_listener_mask_prepared_v1", "fps": float(manifest["fps"]), "provenance": PROVENANCE, "eyelid_channels_filtered": sorted(EYELID_AUS), "unmapped_expressive_eyelid_aus": list(unmapped_expressive_eyelid_aus())}
    for alias in actors:
        row = character_mappings.get(alias) or {}
        rig = str(row.get("maya_node") or "").strip()
        if not rig or not cmds_module.objExists(rig):
            raise RuntimeError(f"{alias}: mapped Maya rig does not exist: {rig}")
        facs = qualify_rig_control(rig, "FACSMaster")
        source_plug = f"{facs}.FACS_animationSource"
        if not cmds_module.objExists(source_plug):
            raise RuntimeError(f"{alias}: missing {source_plug}.")
        add_index = _enum_index(facs, "FACS_animationSource", "Add", cmds_module)
        plugs = [qualify_rig_control(rig, plug) for plug in AU_TO_USER_CONTROL.values()]
        missing = [plug for plug in plugs if not cmds_module.objExists(plug)]
        if missing:
            raise RuntimeError(f"{alias}: missing User FACS controls: {', '.join(missing)}")
        events = [item for item in timeline[alias] if item["state"] != "NONE"]
        scene_range = None
        if hasattr(cmds_module, "playbackOptions"):
            scene_range = (float(cmds_module.playbackOptions(query=True, minTime=True)), float(cmds_module.playbackOptions(query=True, maxTime=True)))
        prepared[alias] = {"rig": rig, "facs_source_plug": source_plug, "add_index": add_index, "managed_user_plugs": plugs, "timeline": timeline[alias], "key_schedule": build_listener_mask_key_schedule(timeline[alias], fps=float(manifest["fps"])), "listener_mask_events": len(events), "layer": _listener_layer_name(alias), "scene_range": scene_range}
    return prepared


def _clear_listener_layer_keys(layer: str, plugs: Iterable[str], *, scene_range: tuple[float, float] | None, cmds_module: Any, override: bool = False) -> None:
    """Clear only curves belonging to a JALITEST-owned animation layer."""
    if not cmds_module.objExists(layer):
        cmds_module.animLayer(layer, override=override)
    elif override:
        # Repair layers created by prior JALITEST versions as additive layers.
        cmds_module.animLayer(layer, edit=True, override=True)
    for plug in plugs:
        cmds_module.animLayer(layer, edit=True, attribute=plug)
    for curve in cmds_module.animLayer(layer, query=True, animCurves=True) or []:
        kwargs: dict[str, Any] = {"clear": True}
        if scene_range is not None:
            kwargs["time"] = scene_range
        cmds_module.cutKey(curve, **kwargs)


def apply_dual_listener_mask_artifacts(*, prepared_context: dict[str, Any], cmds_module: Any | None = None) -> dict[str, Any]:
    """Apply the already-preflighted eyelid-filtered User Mask timeline."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    if prepared_context.get("schema_version") != "dual_listener_mask_prepared_v1":
        raise ValueError("Invalid prepared listener Mask context.")
    result: dict[str, Any] = {"provenance": prepared_context["provenance"]}
    for alias in (name for name in prepared_context if isinstance(prepared_context.get(name), dict) and "facs_source_plug" in prepared_context[name]):
        item = prepared_context.get(alias)
        if not isinstance(item, dict):
            raise ValueError(f"Prepared listener Mask context is missing {alias}.")
        # Both actors were completely validated before this first mutation.
        cmds_module.setAttr(item["facs_source_plug"], item["add_index"])
        _clear_listener_layer_keys(item["layer"], item["managed_user_plugs"], scene_range=item["scene_range"], cmds_module=cmds_module)
        for key in item["key_schedule"]:
            for raw_plug, value in key["pose"].items():
                plug = qualify_rig_control(item["rig"], raw_plug)
                cmds_module.setKeyframe(plug, time=key["frame"], value=value, animLayer=item["layer"])
        result[alias] = {"listener_mask_events": item["listener_mask_events"], "managed_user_plugs": list(item["managed_user_plugs"]), "eyelid_channels_filtered": True, "FACS_animationSource": "Add", "layer": item["layer"], "key_count": len(item["key_schedule"])}
    return result


def prepare_dual_gaze_only_artifacts(*, manifest_path: str | Path, character_mappings: dict[str, dict[str, Any]], cmds_module: Any | None = None) -> dict[str, Any]:
    """Read-only gaze preflight; deliberately has no blink/lid/head path."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    manifest = load_dual_animation_manifest(manifest_path)
    prepared: dict[str, Any] = {"schema_version": "dual_gaze_only_prepared_v1", "fps": float(manifest["fps"]), "jsync_nodes": {}}
    for alias in manifest.get("characters", ["A", "B"]):
        row, runtime = character_mappings.get(alias) or {}, manifest["character_runtime_mapping"][alias]
        rig = str(row.get("maya_node") or "")
        if not rig or not cmds_module.objExists(rig): raise RuntimeError(f"{alias}: mapped Maya rig does not exist: {rig}")
        jsync = resolve_jsync_for_character(rig, str(runtime["sound_file"]), cmds_module=cmds_module)
        events_path = (manifest["artifacts"].get("characters", {}).get(alias, {}) or {}).get("semantic_events") or manifest["artifacts"].get(alias)
        events = json.loads(Path(events_path).read_text(encoding="utf-8")).get("events", [])
        gaze = adapt_dual_gaze_events(events, character_names=manifest.get("characters", ()))
        # Neutral is a rig convention, not an artist-authored calibration.
        # Deliberately ignore legacy gaze_reference/dual_gaze_neutrals data.
        reference = capture_character_gaze_reference(rig, cmds_module=cmds_module)
        positions: dict[str, list[float]] = {}
        for event in gaze:
            target = event["target"]
            if target in {"__BASE__", "DOWN", "UP", "LEFT", "RIGHT", "DOWN_LEFT", "DOWN_RIGHT", "UP_LEFT", "UP_RIGHT"}: continue
            value = (row.get("gaze_targets") or {}).get(target)
            if not isinstance(value, dict) or not isinstance(value.get("eye_stare_translate"), (list, tuple)) or len(value["eye_stare_translate"]) != 3:
                raise ValueError(f"Missing calibrated look-at for {alias} -> {target}.")
            positions[target] = [float(item) for item in value["eye_stare_translate"]]
        schedule = build_dual_gaze_schedule(gaze, neutral_position=reference["eye_stare_translate"], neutral_eyes=reference["both_eyes_translate"], target_positions=positions)
        prepared["jsync_nodes"][alias] = jsync
        plugs = [f"{reference['eye_stare_node']}.translate{axis}" for axis in "XYZ"] + [f"{reference['both_eyes_node']}.translateX", f"{reference['both_eyes_node']}.translateY"]
        if any(not cmds_module.objExists(plug) for plug in plugs): raise RuntimeError(f"{alias}: required gaze controls do not exist.")
        prepared[alias] = {"reference": reference, "schedule": schedule, "keys": build_dual_gaze_key_schedule(schedule, fps=float(manifest["fps"]), transition_frames=4), "gaze_events": len(gaze), "layer": gaze_layer_name(alias), "managed_gaze_plugs": plugs}
    return prepared


def freeze_dual_jsync_nodes(prepared_context: dict[str, Any], *, cmds_module: Any | None = None) -> None:
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    for alias in (name for name in prepared_context if isinstance(prepared_context.get(name), dict) and "reference" in prepared_context[name]):
        cmds_module.delete(prepared_context["jsync_nodes"][alias])


def apply_dual_gaze_only_artifacts(*, prepared_context: dict[str, Any], cmds_module: Any | None = None) -> dict[str, Any]:
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    if prepared_context.get("schema_version") != "dual_gaze_only_prepared_v1": raise ValueError("Invalid prepared gaze-only context.")
    result: dict[str, Any] = {}
    actor_contexts = (
        (actor, item) for actor, item in prepared_context.items()
        if isinstance(item, dict) and "reference" in item and "layer" in item
    )
    for alias, item in actor_contexts:
        reference = item["reference"]
        _clear_listener_layer_keys(item["layer"], item["managed_gaze_plugs"], scene_range=None, cmds_module=cmds_module, override=True)
        for state in item["keys"]:
            for axis, value in zip("XYZ", state["eye_stare"]): cmds_module.setKeyframe(reference["eye_stare_node"], attribute=f"translate{axis}", time=state["frame"], value=value, animLayer=item["layer"])
            cmds_module.setKeyframe(reference["both_eyes_node"], attribute="translateX", time=state["frame"], value=state["eyes"][0], animLayer=item["layer"])
            cmds_module.setKeyframe(reference["both_eyes_node"], attribute="translateY", time=state["frame"], value=state["eyes"][1], animLayer=item["layer"])
        result[alias] = {"gaze_events": item["gaze_events"], "key_count": len(item["keys"]), "layer": item["layer"], "managed_gaze_plugs": item["managed_gaze_plugs"]}
    return result


def apply_dual_speaker_emotion_artifacts(*, manifest_path: str | Path, character_mappings: dict[str, dict[str, Any]], cmds_module: Any | None = None, mel_module: Any | None = None) -> dict[str, Any]:
    """Apply native JALI speaker Mask only; no Heart or overlay channels."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    if mel_module is None:
        from maya import mel as mel_module  # type: ignore
    manifest_file = Path(manifest_path); manifest = load_dual_animation_manifest(manifest_file); prepared: dict[str, Any] = {}
    for alias in manifest.get("characters", ["A", "B"]):
        row=character_mappings.get(alias) or {}; rig=str(row.get("maya_node") or ""); runtime=manifest["character_runtime_mapping"][alias]
        if not rig or not cmds_module.objExists(rig): raise RuntimeError(f"{alias}: mapped Maya rig does not exist: {rig}")
        jsync=resolve_jsync_for_character(rig, str(runtime["sound_file"]), cmds_module=cmds_module)
        if cmds_module.getAttr(f"{jsync}.sound_file") != runtime["sound_file"]: raise RuntimeError(f"{alias}: jSync sound_file mismatch before mutation.")
        prefix=_rig_namespace(rig) + ":" if _rig_namespace(rig) else ""
        if not cmds_module.objExists(prefix+"altFACSMaster"): raise RuntimeError(f"{alias}: missing rig control {prefix}altFACSMaster.")
        character_artifacts = manifest["artifacts"].get("characters", {})
        artifact=Path((character_artifacts.get(alias) or {}).get("jali_speaker_annotated") or manifest["artifacts"].get(f"{alias}_jali_speaker_annotated") or "")
        diagnostic=Path((character_artifacts.get(alias) or {}).get("jali_speaker_annotation") or manifest["artifacts"].get(f"{alias}_jali_speaker_annotation") or "")
        if not artifact.is_file() or not diagnostic.is_file(): raise FileNotFoundError(f"{alias}: dual speaker emotion artifacts are missing.")
        info=json.loads(diagnostic.read_text(encoding="utf-8")); mask=bool(info.get("mask_tag_count"))
        attrs=("calculate_paralinguals","paralingual_bearing","paralingual_intensity","override_annotation","calculate_blinks","transcript","text_input_path","sound_input_path","output_path")
        if manifest.get("schema_version") == "dual_animation_manifest_v0":
            attrs += ("calculate_expression", "expression_source", "expression_strength")
        missing=[attr for attr in attrs if not cmds_module.objExists(f"{jsync}.{attr}")]
        if missing: raise RuntimeError(f"{alias}: jSync is missing required attributes: {', '.join(missing)}")
        wav=Path(str((manifest.get("wav_durations",{}).get(alias,{}) or {}).get("path") or ""))
        if not wav.is_file(): raise FileNotFoundError(f"{alias}: original runtime WAV not found: {wav}")
        if not mel_module.eval('exists "realign_node"'): raise RuntimeError("Installed JALI realign_node procedure is unavailable.")
        stage=manifest_file.parent/"jali_runtime"/alias
        original={attr: cmds_module.getAttr(f"{jsync}.{attr}") for attr in ("text_input_path","sound_input_path","output_path")}
        prepared[alias]={"rig":rig,"jsync":jsync,"leaf":jsync.rsplit("|",1)[-1],"prefix":prefix,"artifact":artifact,"wav":wav,"stage":stage,"original_paths":original,"info":info,"mask":mask,"mask_bearing":_enum_index(jsync,"paralingual_bearing","from Annotation",cmds_module) if mask else None,"mask_intensity":_enum_index(jsync,"paralingual_intensity","From Transcript Tags",cmds_module) if mask else None}
    selection=cmds_module.ls(selection=True, long=True) or []
    result: dict[str, Any] = {}
    changed: list[dict[str, Any]] = []
    try:
        for alias, item in prepared.items():
            jsync=item["jsync"]; prefix=item["prefix"]; mask=item["mask"]
            item["stage"].mkdir(parents=True,exist_ok=True); staged_txt=item["stage"] / f"{Path(str(manifest['character_runtime_mapping'][alias]['sound_file'])).name}.txt"; staged_wav=item["stage"] / f"{Path(str(manifest['character_runtime_mapping'][alias]['sound_file'])).name}.wav"
            shutil.copy2(item["artifact"],staged_txt); shutil.copy2(item["wav"],staged_wav)
            settings = [("calculate_paralinguals", mask), ("override_annotation", False), ("calculate_blinks", False)]
            for attr, value in settings:
                cmds_module.setAttr(f"{jsync}.{attr}", value)
            if mask: cmds_module.setAttr(f"{jsync}.paralingual_bearing",item["mask_bearing"]); cmds_module.setAttr(f"{jsync}.paralingual_intensity",item["mask_intensity"])
            cmds_module.setAttr(f"{jsync}.transcript",item["artifact"].read_text(encoding="utf-8"),type="string")
            for attr in item["original_paths"]: cmds_module.setAttr(f"{jsync}.{attr}",str(item["stage"])+os.sep,type="string")
            changed.append(item); mel_module.eval(f'realign_node "{item["leaf"]}";')
            if mask: mel_module.eval(f'jali_set_myofAnimation "{jsync}" "{prefix}" 0;')
            result[alias]={**item["info"],"maya_node":item["rig"],"jsync_node":jsync,"rig_prefix":prefix,"staging_dir":str(item["stage"]),"staging_txt":str(staged_txt),"staging_wav":str(staged_wav),"realign_completed":True,"paths_restored":False,"calculate_paralinguals":mask,"calculate_blinks":cmds_module.getAttr(f"{jsync}.calculate_blinks"),"mask_binding":mask,"warnings":[]}
    finally:
     for item in changed:
        for attr,value in item["original_paths"].items(): cmds_module.setAttr(f"{item['jsync']}.{attr}",value,type="string")
     cmds_module.select(selection,replace=True) if selection else cmds_module.select(clear=True)
     for alias in result: result[alias]["paths_restored"]=True
    return result


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
    if isinstance(value, dict) and value.get("schema_version") == "dual_animation_manifest_v0":
        mapping, artifacts = value.get("character_runtime_mapping"), value.get("artifacts")
        if not isinstance(mapping, dict) or not isinstance(artifacts, dict):
            raise ValueError("Dual animation manifest requires character_runtime_mapping and artifacts.")
        for alias in ("A", "B"):
            if not isinstance(mapping.get(alias), dict) or not str(mapping[alias].get("sound_file") or ""):
                raise ValueError(f"Dual animation manifest requires {alias} runtime mapping.")
            artifact_path = Path(str(artifacts.get(alias) or ""))
            if not artifact_path.is_absolute(): artifact_path = REPO_ROOT / artifact_path
            if not artifact_path.is_file(): raise FileNotFoundError(f"Dual semantic artifact for {alias} is missing: {artifact_path}")
            artifacts[alias] = str(artifact_path)
        return value
    if not isinstance(value, dict) or value.get("schema_version") not in {"dual_animation_manifest_v1", "dual_animation_manifest_v2"}:
        raise ValueError(f"Invalid dual animation manifest: {path}")
    mapping, artifacts = value.get("character_runtime_mapping"), value.get("artifacts")
    characters = value.get("characters")
    if not isinstance(mapping, dict) or not isinstance(artifacts, dict) or not isinstance(characters, list):
        raise ValueError("Dual animation manifest requires character_runtime_mapping and artifacts.")
    character_artifacts = artifacts.get("characters")
    if not isinstance(character_artifacts, dict):
        raise ValueError("Dual animation manifest requires name-keyed character artifacts.")
    for alias in characters:
        if not isinstance(mapping.get(alias), dict) or str(mapping[alias].get("script_name") or "") != alias or not str(mapping[alias].get("sound_file") or ""):
            raise ValueError(f"Dual animation manifest requires named runtime mapping for {alias}.")
        artifact_key = "resolved_sparse_events" if value.get("schema_version") == "dual_animation_manifest_v2" else "semantic_events"
        artifact_path = Path(str((character_artifacts.get(alias) or {}).get(artifact_key) or ""))
        if not artifact_path.is_absolute():
            artifact_path = REPO_ROOT / artifact_path
        if not artifact_path.is_file():
            raise FileNotFoundError(f"Dual semantic artifact for {alias} is missing: {artifact_path}")
        character_artifacts[alias][artifact_key] = str(artifact_path)
    timing_path = Path(str(artifacts.get("conversation_anchor_timing") or ""))
    if timing_path and not timing_path.is_absolute():
        artifacts["conversation_anchor_timing"] = str(REPO_ROOT / timing_path)
    return value


def _v2_overlay_config(path: str | Path = DEFAULT_MAYA_CONFIG) -> dict[str, Any]:
    import yaml
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    head = value.get("maya_head_overlay")
    blink = value.get("maya_performative_blink_overlay")
    if not isinstance(head, dict) or not isinstance(blink, dict):
        raise ValueError("Maya config requires maya_head_overlay and maya_performative_blink_overlay.")
    return {"head": head, "blink": blink}


def head_layer_name(actor: str) -> str:
    return f"{HEAD_LAYER_PREFIX}{actor}"


def blink_layer_name(actor: str) -> str:
    return f"{BLINK_LAYER_PREFIX}{actor}"


def _head_target(value: str, config: dict[str, Any]) -> dict[str, float]:
    target = {"rotateX": 0.0, "rotateY": 0.0, "rotateZ": 0.0}
    if value == "HEAD-NONE":
        return target
    match = re.fullmatch(r"HEAD-(UP|DOWN|TILT_LEFT|TILT_RIGHT)-(SUBTLE|MEDIUM|STRONG)", value)
    if not match:
        raise ValueError(f"Unsupported v2 head pose: {value}")
    direction, strength = match.groups()
    degrees = float((config.get("strength_degrees") or {})[strength])
    if direction in {"UP", "DOWN"}:
        axis = str(config["pitch_axis"])
        sign = float(config["pitch_up_sign"]) * (1.0 if direction == "UP" else -1.0)
    else:
        axis = str(config["roll_axis"])
        sign = float(config["tilt_left_sign"]) * (1.0 if direction == "TILT_LEFT" else -1.0)
    if axis not in target:
        raise ValueError(f"Configured head axis must be rotateX/Y/Z, got {axis!r}")
    target[axis] = sign * degrees
    return target


def build_head_overlay_key_schedule(events: Iterable[dict[str, Any]], *, fps: float, config: dict[str, Any]) -> list[dict[str, Any]]:
    transition = int(config.get("transition_frames", 4))
    previous = {"rotateX": 0.0, "rotateY": 0.0, "rotateZ": 0.0}
    keys: list[dict[str, Any]] = []
    for event in events:
        value = (event.get("changes") or {}).get("head")
        if not value:
            continue
        frame = float(event["resolved_start"]) * float(fps)
        target = _head_target(str(value), config)
        role = str(event.get("timing_role") or "SPEAK_ONSET")
        if role == "INITIAL_STATE":
            keys.append({"frame": 0.0, "values": target, "event_id": event.get("event_id")})
        elif role == "LISTEN_REACTION":
            keys.append({"frame": frame, "values": dict(previous), "event_id": event.get("event_id")})
            keys.append({"frame": frame + transition, "values": target, "event_id": event.get("event_id")})
        else:
            keys.append({"frame": max(0.0, frame - transition), "values": dict(previous), "event_id": event.get("event_id")})
            keys.append({"frame": frame, "values": target, "event_id": event.get("event_id")})
        previous = target
    return keys


def build_blink_overlay_key_schedule(events: Iterable[dict[str, Any]], *, fps: float, config: dict[str, Any]) -> list[dict[str, Any]]:
    opened, closed = float(config.get("open_value", 0)), float(config.get("closed_value", 1))
    presets = config.get("presets") or {}
    keys: list[dict[str, Any]] = []
    hold_active = False
    for event in events:
        value = (event.get("changes") or {}).get("blink")
        if not value:
            continue
        cursor = float(event["resolved_start"]) * float(fps)
        if value == "EYE_CLOSE_HOLD":
            if hold_active:
                raise ValueError("EYE_CLOSE_HOLD requires EYE_OPEN before another hold.")
            preset = presets.get(value) or {}
            keys.extend([
                {"frame": cursor, "value": opened, "event_id": event.get("event_id")},
                {"frame": cursor + int(preset.get("close_frames", 4)), "value": closed, "event_id": event.get("event_id")},
            ])
            hold_active = True
            continue
        if value == "EYE_OPEN":
            if not hold_active:
                raise ValueError("EYE_OPEN requires an active EYE_CLOSE_HOLD.")
            keys.append({"frame": cursor, "value": opened, "event_id": event.get("event_id")})
            hold_active = False
            continue
        preset = presets.get(value)
        if not isinstance(preset, dict):
            raise ValueError(f"Missing performative blink preset {value}")
        for _index in range(int(preset["count"])):
            keys.extend([
                {"frame": cursor, "value": opened, "event_id": event.get("event_id")},
                {"frame": cursor + int(preset["close_frames"]), "value": closed, "event_id": event.get("event_id")},
                {"frame": cursor + int(preset["close_frames"]) + int(preset["hold_frames"]), "value": closed, "event_id": event.get("event_id")},
                {"frame": cursor + int(preset["close_frames"]) + int(preset["hold_frames"]) + int(preset["open_frames"]), "value": opened, "event_id": event.get("event_id")},
            ])
            cursor += int(preset["close_frames"]) + int(preset["hold_frames"]) + int(preset["open_frames"]) + int(preset["gap_frames"])
    return keys


def _affect_identity(value: object) -> str:
    text = str(value or "NONE")
    if text in {"NONE", "MASK-NONE"}:
        return "NONE"
    state, separator, intensity = text.rpartition("-")
    return state if separator and intensity.isdigit() else text


def plan_v2_blinks(events: Iterable[dict[str, Any]], *, initial_state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Plan explicit and deterministic regulatory blinks for one actor.

    Boundaries are coalesced before applying explicit > gaze > affect priority.
    Automatic GLANCE returns are absent from the authored event stream and thus
    cannot generate a second blink.
    """
    ordered = sorted(enumerate(events), key=lambda row: (float(row[1]["resolved_start"]), row[0]))
    groups: list[tuple[float, list[dict[str, Any]]]] = []
    for _index, event in ordered:
        time = float(event["resolved_start"])
        if groups and abs(groups[-1][0] - time) <= 1e-9:
            groups[-1][1].append(event)
        else:
            groups.append((time, [event]))
    gaze_initialized = initial_state is not None
    affect_initialized = initial_state is not None
    gaze_state = str((initial_state or {}).get("gaze", "GAZE-NONE"))
    affect_state = _affect_identity((initial_state or {}).get("affect", "NONE"))
    hold_active = False
    planned: list[dict[str, Any]] = []
    for time, boundary in groups:
        explicit = next((event for event in boundary if (event.get("changes") or {}).get("blink")), None)
        gaze_values = [(event.get("changes") or {}).get("gaze") for event in boundary if "gaze" in (event.get("changes") or {})]
        affect_values = [(event.get("changes") or {}).get("affect") for event in boundary if "affect" in (event.get("changes") or {})]
        authored_gaze = str(gaze_values[-1]) if gaze_values else gaze_state
        final_affect = _affect_identity(affect_values[-1]) if affect_values else affect_state
        gaze_candidate = bool(gaze_values and gaze_initialized and authored_gaze != gaze_state)
        affect_candidate = bool(affect_values and affect_initialized and final_affect != affect_state)
        source_event = explicit or (boundary[-1] if gaze_candidate or affect_candidate else None)
        if explicit is not None:
            blink = str(explicit["changes"]["blink"])
            if blink == "EYE_CLOSE_HOLD":
                if hold_active:
                    raise ValueError("EYE_CLOSE_HOLD requires EYE_OPEN before another hold.")
                hold_active = True
            elif blink == "EYE_OPEN":
                if not hold_active:
                    raise ValueError("EYE_OPEN requires an active EYE_CLOSE_HOLD.")
                hold_active = False
            source = "explicit"
        elif hold_active:
            blink = ""
            source = ""
        elif gaze_candidate:
            blink = "BLINK"
            source = "gaze_regulatory"
        elif affect_candidate:
            blink = "BLINK"
            source = "affect_regulatory"
        else:
            blink = ""
            source = ""
        if blink and source_event is not None:
            planned.append({
                "event_id": source_event.get("event_id"), "actor": source_event.get("actor"),
                "resolved_start": time, "changes": {"blink": blink},
                "blink_source": source,
                "source_event_ids": [event.get("event_id") for event in boundary],
            })
        if gaze_values:
            if not authored_gaze.startswith("GLANCE-"):
                gaze_state = authored_gaze
            gaze_initialized = True
        if affect_values:
            affect_state, affect_initialized = final_affect, True
    return planned


def _resolve_user_blink_plugs(rig: str, config: dict[str, Any], cmds_module: Any) -> list[str]:
    central = f"{qualify_rig_control(rig, str(config['central_control_suffix']))}.{config['central_attribute']}"
    if cmds_module.objExists(central):
        return [central]
    left = f"{qualify_rig_control(rig, str(config['left_control_suffix']))}.{config['left_attribute']}"
    right = f"{qualify_rig_control(rig, str(config['right_control_suffix']))}.{config['right_attribute']}"
    if cmds_module.objExists(left) and cmds_module.objExists(right):
        return [left, right]
    raise RuntimeError(
        f"Mapped rig {rig} has no usable User blink control; expected {central} or both {left}, {right}."
    )


def prepare_dual_v2_head_blink_overlays(
    *, manifest_path: str | Path, character_mappings: dict[str, dict[str, Any]],
    baseline: dict[str, Any], cmds_module: Any | None = None, mel_module: Any | None = None,
    config_path: str | Path = DEFAULT_MAYA_CONFIG,
) -> dict[str, Any]:
    """Read-only, both-character preflight for v2 head/blink overlay mutation."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    if mel_module is None:
        from maya import mel as mel_module  # type: ignore
    manifest = load_dual_animation_manifest(manifest_path)
    if manifest.get("schema_version") != "dual_animation_manifest_v2":
        raise ValueError("Expected dual_animation_manifest_v2.")
    config = _v2_overlay_config(config_path)
    characters = manifest["characters"]
    if set(character_mappings) != set(characters):
        raise ValueError("V2 Maya mapping must be name-keyed for exactly both manifest characters.")
    baseline_preflight = _validate_dual_jali_base(baseline, character_mappings, cmds_module=cmds_module, mel_module=mel_module)
    prepared: dict[str, Any] = {"schema_version": "dual_v2_head_blink_prepared_v1", "fps": float(manifest["fps"]), "actors": {}}
    for actor in characters:
        row = character_mappings[actor]
        runtime = manifest["character_runtime_mapping"][actor]
        if str(row.get("script_name") or "") != actor:
            raise ValueError(f"{actor}: mapping script_name must match manifest identity.")
        rig = str(row.get("maya_node") or "")
        jsync = resolve_jsync_for_character(rig, str(runtime["sound_file"]), cmds_module=cmds_module)
        artifact_row = manifest["artifacts"]["characters"][actor]
        for required in ("resolved_sparse_events", "jali_speaker_annotated", "jali_speaker_annotation"):
            if not Path(str(artifact_row.get(required) or "")).is_file():
                raise FileNotFoundError(f"{actor}: required v2 artifact {required} is missing.")
        payload = json.loads(Path(artifact_row["resolved_sparse_events"]).read_text(encoding="utf-8"))
        events = payload.get("events", [])
        initial_state = ((payload.get("initial_state") or {}).get("state") or {})
        head_events = [event for event in events if "head" in (event.get("changes") or {})]
        if initial_state.get("head") not in (None, "NONE", "HEAD-NONE"):
            head_events = [{"event_id": "INITIAL_STATE", "actor": actor, "timing_role": "INITIAL_STATE", "resolved_start": 0.0, "changes": {"head": initial_state["head"]}}] + head_events
        blink_events = plan_v2_blinks(events, initial_state=initial_state)
        neck = qualify_rig_control(rig, str(config["head"]["control_suffix"]))
        head_plugs = [f"{neck}.rotate{axis}" for axis in "XYZ"]
        if head_events and any(not cmds_module.objExists(plug) for plug in head_plugs):
            raise RuntimeError(f"{actor}: head event requires {neck}.rotateX/Y/Z.")
        blink_plugs = _resolve_user_blink_plugs(rig, config["blink"], cmds_module) if blink_events else []
        vendor_blink_plug = f"{qualify_rig_control(rig, str(config['blink']['vendor_output_control_suffix']))}.{config['blink']['vendor_output_attribute']}"
        if not cmds_module.objExists(vendor_blink_plug):
            raise RuntimeError(f"{actor}: cannot verify JALI blink ownership; missing vendor output {vendor_blink_plug}.")
        facs = qualify_rig_control(rig, "FACSMaster")
        facs_plug = f"{facs}.FACS_animationSource"
        if blink_events and not cmds_module.objExists(facs_plug):
            raise RuntimeError(f"{actor}: performative blink requires {facs_plug}.")
        prepared["actors"][actor] = {
            "rig": rig, "jsync": jsync, "head_layer": head_layer_name(actor), "blink_layer": blink_layer_name(actor),
            "head_plugs": head_plugs, "blink_plugs": blink_plugs, "facs_plug": facs_plug,
            "vendor_blink_plug": vendor_blink_plug,
            "facs_add_index": _enum_index(facs, "FACS_animationSource", "Add", cmds_module) if blink_events else None,
            "head_keys": build_head_overlay_key_schedule(head_events, fps=float(manifest["fps"]), config=config["head"]),
            "blink_plan": blink_events,
            "blink_keys": build_blink_overlay_key_schedule(blink_events, fps=float(manifest["fps"]), config=config["blink"]),
            "baseline": baseline_preflight[actor],
        }
    return prepared


def apply_dual_v2_head_blink_overlays(*, prepared_context: dict[str, Any], cmds_module: Any | None = None) -> dict[str, Any]:
    """Apply only JALITEST-owned additive head and performative blink layers."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    if prepared_context.get("schema_version") != "dual_v2_head_blink_prepared_v1":
        raise ValueError("Invalid prepared v2 overlay context.")
    result: dict[str, Any] = {}
    for actor, item in prepared_context["actors"].items():
        if item["head_keys"]:
            _clear_listener_layer_keys(item["head_layer"], item["head_plugs"], scene_range=None, cmds_module=cmds_module, override=False)
            for key in item["head_keys"]:
                for plug in item["head_plugs"]:
                    attr = plug.rsplit(".", 1)[1]
                    cmds_module.setKeyframe(plug.rsplit(".", 1)[0], attribute=attr, time=key["frame"], value=key["values"][attr], animLayer=item["head_layer"])
        if item["blink_keys"]:
            _clear_listener_layer_keys(item["blink_layer"], item["blink_plugs"], scene_range=None, cmds_module=cmds_module, override=False)
            cmds_module.setAttr(item["facs_plug"], item["facs_add_index"])
            for key in item["blink_keys"]:
                for plug in item["blink_plugs"]:
                    node, attr = plug.rsplit(".", 1)
                    cmds_module.setKeyframe(node, attribute=attr, time=key["frame"], value=key["value"], animLayer=item["blink_layer"])
        result[actor] = {"head_layer": item["head_layer"], "blink_layer": item["blink_layer"], "head_key_count": len(item["head_keys"]), "blink_key_count": len(item["blink_keys"]), "blink_plan": item.get("blink_plan", []), "jali_calculate_blinks_disabled": True}
    return result


def _load_v2_actor_events(manifest: dict[str, Any], actor: str) -> list[dict[str, Any]]:
    path = Path(manifest["artifacts"]["characters"][actor]["resolved_sparse_events"])
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value.get("events"), list):
        raise ValueError(f"{actor}: resolved_sparse_events requires an events list.")
    return value["events"]


def _load_v2_actor_initial_state(manifest: dict[str, Any], actor: str) -> dict[str, Any]:
    path = Path(manifest["artifacts"]["characters"][actor]["resolved_sparse_events"])
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(((value.get("initial_state") or {}).get("state") or {}))


def prepare_dual_v2_listener_mask_artifacts(*, manifest_path: str | Path, character_mappings: dict[str, dict[str, Any]], cmds_module: Any | None = None) -> dict[str, Any]:
    """Build persistent v2 listener Mask handoff schedules without shared phrases."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    manifest = load_dual_animation_manifest(manifest_path)
    if manifest.get("schema_version") != "dual_animation_manifest_v2":
        raise ValueError("Expected dual_animation_manifest_v2.")
    anchor_times = json.loads(Path(manifest["artifacts"]["conversation_anchor_timing"]).read_text(encoding="utf-8"))
    actors = manifest["characters"]
    prepared: dict[str, Any] = {"schema_version": "dual_listener_mask_prepared_v1", "fps": float(manifest["fps"]), "provenance": PROVENANCE, "eyelid_channels_filtered": sorted(EYELID_AUS), "unmapped_expressive_eyelid_aus": list(unmapped_expressive_eyelid_aus())}
    for actor in actors:
        events = _load_v2_actor_events(manifest, actor)
        initial_state = _load_v2_actor_initial_state(manifest, actor)
        points: list[tuple[float, int, str, Any]] = []
        turns: dict[str, list[dict[str, Any]]] = {}
        for anchor_id, anchor in anchor_times.items():
            turns.setdefault(str(anchor.get("turn_id") or anchor_id), []).append(anchor)
        for turn in turns.values():
            ordered_turn = sorted(turn, key=lambda anchor: (float(anchor["start"]), float(anchor["end"])))
            if str(ordered_turn[0]["speaker"]) == actor:
                points.append((float(ordered_turn[0]["start"]), 2, "turn_start", actor))
                points.append((float(ordered_turn[-1]["end"]), 1, "turn_end", actor))
        for event in events:
            if "affect" in (event.get("changes") or {}):
                points.append((float(event["resolved_start"]), 0, "affect", event))
        initial_affect = initial_state.get("affect", "MASK-NONE")
        name, intensity = parse_mask_state("NONE" if initial_affect == "MASK-NONE" else initial_affect)
        affect = "NONE" if name == "NONE" else f"{name}-{intensity:g}"
        # Each actor owns its own speaking state; overlapping dialogue is valid.
        is_speaking = False
        intervals: list[dict[str, Any]] = [{"phrase_id": "INITIAL_STATE", "speaker": None, "start": 0.0, "end": float(manifest["shared_duration_seconds"]), "state": affect, "pose": user_pose_for_mask(affect), "boundary_kind": "INITIAL_STATE", "timing_role": "INITIAL_STATE"}]
        for time, _priority, kind, value in sorted(points):
            if kind == "affect":
                name, intensity = parse_mask_state("NONE" if value["changes"]["affect"] == "MASK-NONE" else value["changes"]["affect"])
                affect = "NONE" if name == "NONE" else f"{name}-{intensity:g}"
            elif kind == "turn_end":
                is_speaking = False
            else:
                is_speaking = True
            state = "NONE" if is_speaking else affect
            metadata = {"boundary_kind": kind, "timing_role": value.get("timing_role") if kind == "affect" else None}
            if intervals and intervals[-1]["start"] == time:
                intervals[-1].update({"state": state, "pose": user_pose_for_mask(state), **metadata})
            elif not intervals or intervals[-1]["state"] != state:
                intervals.append({"phrase_id": f"v2@{time:g}", "speaker": actor if is_speaking else None, "start": time, "end": float(manifest["shared_duration_seconds"]), "state": state, "pose": user_pose_for_mask(state), **metadata})
        for index, interval in enumerate(intervals[:-1]):
            interval["end"] = intervals[index + 1]["start"]
        row = character_mappings.get(actor) or {}
        rig = str(row.get("maya_node") or "")
        if not rig or not cmds_module.objExists(rig):
            raise RuntimeError(f"{actor}: mapped Maya rig does not exist: {rig}")
        facs = qualify_rig_control(rig, "FACSMaster")
        source_plug = f"{facs}.FACS_animationSource"
        if not cmds_module.objExists(source_plug):
            raise RuntimeError(f"{actor}: missing {source_plug}.")
        plugs = [qualify_rig_control(rig, plug) for plug in AU_TO_USER_CONTROL.values()]
        missing = [plug for plug in plugs if not cmds_module.objExists(plug)]
        if missing:
            raise RuntimeError(f"{actor}: missing User FACS controls: {', '.join(missing)}")
        prepared[actor] = {"rig": rig, "facs_source_plug": source_plug, "add_index": _enum_index(facs, "FACS_animationSource", "Add", cmds_module), "managed_user_plugs": plugs, "timeline": intervals, "key_schedule": build_v2_listener_mask_key_schedule(intervals, fps=float(manifest["fps"])), "listener_mask_events": sum(row["state"] != "NONE" for row in intervals), "layer": _listener_layer_name(actor), "scene_range": None}
    return prepared


def prepare_dual_v2_gaze_only_artifacts(*, manifest_path: str | Path, character_mappings: dict[str, dict[str, Any]], cmds_module: Any | None = None) -> dict[str, Any]:
    """Preflight calibrated persistent v2 gaze for both named actors."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    manifest = load_dual_animation_manifest(manifest_path)
    if manifest.get("schema_version") != "dual_animation_manifest_v2":
        raise ValueError("Expected dual_animation_manifest_v2.")
    source_path = str(REPO_ROOT / "src")
    if source_path not in sys.path: sys.path.insert(0, source_path)
    from expregaze_jali.maya_apply_gaze import load_maya_gaze_config  # noqa: PLC0415
    gaze_config = load_maya_gaze_config(DEFAULT_MAYA_CONFIG)
    prepared: dict[str, Any] = {"schema_version": "dual_gaze_only_prepared_v1", "fps": float(manifest["fps"]), "jsync_nodes": {}}
    directions = {"RIGHT", "LEFT", "DOWN", "DOWN_LEFT", "DOWN_RIGHT", "UP", "UP_LEFT", "UP_RIGHT"}
    for actor in manifest["characters"]:
        row = character_mappings.get(actor) or {}
        rig = str(row.get("maya_node") or "")
        runtime = manifest["character_runtime_mapping"][actor]
        prepared["jsync_nodes"][actor] = resolve_jsync_for_character(rig, str(runtime["sound_file"]), cmds_module=cmds_module)
        raw = []
        gaze_rows = [event for event in _load_v2_actor_events(manifest, actor) if "gaze" in (event.get("changes") or {})]
        initial_gaze = _load_v2_actor_initial_state(manifest, actor).get("gaze", "GAZE-NONE")
        if initial_gaze != "GAZE-NONE":
            gaze_rows = [{"event_id": "INITIAL_STATE", "resolved_start": 0.0, "changes": {"gaze": initial_gaze}, "reason": None}] + gaze_rows
        for index, event in enumerate(gaze_rows):
            value = str(event["changes"]["gaze"])
            start = float(event["resolved_start"])
            if value == "GAZE-NONE":
                mode, target = "RESET", "__BASE__"
            else:
                mode, target = value.split("-", 1)
                if mode not in {"GAZE", "GLANCE"}:
                    raise ValueError(f"{actor}: v2 executable gaze mode must be GAZE or GLANCE, got {mode!r}.")
            if mode == "GLANCE":
                transition = float(gaze_config.get("glance_transition_frames", 3)) / float(manifest["fps"])
                end = start + transition + float(gaze_config.get("glance_hold_seconds", 0.5)) + transition
            else:
                end = float(gaze_rows[index + 1]["resolved_start"]) if index + 1 < len(gaze_rows) else float(manifest["shared_duration_seconds"])
            raw.append({"id": event["event_id"], "phrase_id": event["event_id"], "reason": event.get("reason"), "type": "gaze", "mode": mode, "target": target, "social_avert": False, "resolved_time": {"start": start, "end": end}})
        reference = capture_character_gaze_reference(rig, cmds_module=cmds_module)
        positions: dict[str, list[float]] = {}
        for event in raw:
            target = event["target"]
            if event["mode"] in {"GAZE", "GLANCE"} and target not in directions:
                positions[target] = resolve_actor_target_position(actor, target, character_mappings)
        schedule = build_dual_gaze_schedule(raw, neutral_position=reference["eye_stare_translate"], neutral_eyes=reference["both_eyes_translate"], target_positions=positions, magnitude=float(gaze_config.get("directional_eye_magnitude", 5)), limit=float(gaze_config.get("directional_eye_limit", 6)))
        plugs = [*(f"{reference['eye_stare_node']}.translate{axis}" for axis in "XYZ"), f"{reference['both_eyes_node']}.translateX", f"{reference['both_eyes_node']}.translateY"]
        if any(not cmds_module.objExists(plug) for plug in plugs):
            raise RuntimeError(f"{actor}: required gaze controls do not exist.")
        prepared[actor] = {"reference": reference, "schedule": schedule, "keys": build_dual_gaze_key_schedule(schedule, fps=float(manifest["fps"]), transition_frames=int(gaze_config.get("gaze_transition_frames", 3)), glance_transition_frames=int(gaze_config.get("glance_transition_frames", 3)), glance_hold_seconds=float(gaze_config.get("glance_hold_seconds", 0.5)), allow_shortened_glance=True), "gaze_events": len(raw), "layer": gaze_layer_name(actor), "managed_gaze_plugs": plugs}
    return prepared


def diagnose_head_local_axes(character_node: str, *, degrees: float = 5.0, cmds_module: Any | None = None, config_path: str | Path = DEFAULT_MAYA_CONFIG) -> None:
    """Interactive Maya probe: print local jNeck rotations at +/- degrees, then restore."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    config = _v2_overlay_config(config_path)["head"]
    neck = qualify_rig_control(character_node, str(config["control_suffix"]))
    plugs = [f"{neck}.rotate{axis}" for axis in "XYZ"]
    if any(not cmds_module.objExists(plug) for plug in plugs):
        raise RuntimeError(f"Missing diagnostic neck control: {neck}.rotateX/Y/Z")
    original = [cmds_module.getAttr(plug) for plug in plugs]
    try:
        for axis, plug in zip("XYZ", plugs):
            for sign in (-1.0, 1.0):
                cmds_module.setAttr(plug, original["XYZ".index(axis)] + sign * float(degrees))
                print(f"{neck} local rotate{axis} {sign * float(degrees):+g}: {[cmds_module.getAttr(item) for item in plugs]}")
            cmds_module.setAttr(plug, original["XYZ".index(axis)])
    finally:
        for plug, value in zip(plugs, original):
            cmds_module.setAttr(plug, value)


def diagnose_v2_blink_ownership(*, prepared_context: dict[str, Any], cmds_module: Any | None = None, strict: bool = True) -> dict[str, Any]:
    """Post-Generate Maya probe for exclusive JALITEST v2 blink ownership.

    The configured vendor output is the repository's known JALI blink/lid path.
    If a production rig uses another output, preflight fails and the config must
    be corrected from a real Maya observation rather than inferred in Python.
    """
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    if prepared_context.get("schema_version") != "dual_v2_head_blink_prepared_v1":
        raise ValueError("Invalid prepared v2 overlay context.")
    report: dict[str, Any] = {"schema_version": "dual_v2_blink_ownership_diagnostic_v1", "actors": {}, "passed": True}
    problems: list[str] = []
    for actor, item in prepared_context["actors"].items():
        calculate_blinks = bool(cmds_module.getAttr(f"{item['jsync']}.calculate_blinks"))
        owned_curves = set(cmds_module.animLayer(item["blink_layer"], query=True, animCurves=True) or []) if cmds_module.objExists(item["blink_layer"]) else set()
        vendor_curves = set(cmds_module.listConnections(item["vendor_blink_plug"], source=True, destination=False, type="animCurve") or [])
        user_curves = {
            curve for plug in item["blink_plugs"]
            for curve in (cmds_module.listConnections(plug, source=True, destination=False, type="animCurve") or [])
        }
        foreign_user_curves = sorted(user_curves - owned_curves)
        actor_passed = not calculate_blinks and not vendor_curves and not foreign_user_curves
        report["actors"][actor] = {
            "jsync": item["jsync"], "calculate_blinks": calculate_blinks,
            "vendor_output_plug": item["vendor_blink_plug"],
            "vendor_anim_curves": sorted(vendor_curves),
            "jalitest_layer": item["blink_layer"], "jalitest_anim_curves": sorted(owned_curves),
            "foreign_user_blink_curves": foreign_user_curves, "passed": actor_passed,
        }
        if calculate_blinks:
            problems.append(f"{actor}: jSync.calculate_blinks is not False")
        if vendor_curves:
            problems.append(f"{actor}: vendor blink output still has animCurve contribution(s): {sorted(vendor_curves)}")
        if foreign_user_curves:
            problems.append(f"{actor}: User blink controls have curves outside {item['blink_layer']}: {foreign_user_curves}")
    report["passed"] = not problems
    if problems and strict:
        raise RuntimeError("V2 blink ownership diagnostic failed after Generate: " + "; ".join(problems))
    return report


def resolve_character_look_at_target(alias: str, character_mappings: dict[str, dict[str, Any]], *, configured_suffix: str | None = None) -> str:
    row = character_mappings.get(alias) or {}
    explicit = str(row.get("look_at_node") or "").strip()
    if explicit:
        return explicit
    root = str(row.get("maya_node") or "").strip()
    if configured_suffix and root:
        return qualify_rig_control(root, configured_suffix)
    raise ValueError(f"Character {alias} requires an explicit look_at_node (JALI_GRP is not a gaze target).")


def adapt_dual_gaze_events(events: Iterable[dict[str, Any]], *, character_names: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Convert generic dual gaze rows to Maya gaze rows in chronological order."""
    adapted=[]
    for event in events:
        if event.get("channel") != "gaze" or not event.get("resolved_time"): continue
        mode, _, target = str(event.get("value") or "").partition("-")
        if not target: continue
        # Social AVERT deliberately returns to base rather than looking at the
        # other character. Explicit AVERT-DOWN/etc retain direction targets.
        names = set(character_names) or {"A", "B"}
        social_avert = mode == "AVERT" and target in names
        resolved_target = "__BASE__" if social_avert else target
        adapted.append({"id": event.get("id") or event.get("phrase_id"), "phrase_id": event.get("phrase_id"), "source_proposal_id": event.get("source_proposal_id"), "reason": event.get("reason"), "type":"gaze", "mode":mode, "target":resolved_target, "social_avert": social_avert, "resolved_time":dict(event["resolved_time"])})
    return sorted(adapted, key=lambda e:(float(e["resolved_time"]["start"]),float(e["resolved_time"]["end"])))


def capture_character_gaze_reference(character_node: str, *, cmds_module: Any | None = None) -> dict[str, Any]:
    """Snapshot automatic baselines and derive the internal forward neutral."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    eye_stare = qualify_rig_control(character_node, "eyeStare_world")
    both_eyes = qualify_rig_control(character_node, "CNT_BOTH_EYES")
    if not cmds_module.objExists(eye_stare) or not cmds_module.objExists(both_eyes):
        raise RuntimeError(f"Could not resolve eyeStare_world/CNT_BOTH_EYES for {character_node}")
    baseline_z = float(cmds_module.getAttr(f"{eye_stare}.translateZ"))
    return {"eye_stare_node": eye_stare, "baseline_translateZ": baseline_z, "eye_stare_translate": [0.0, 0.0, baseline_z], "both_eyes_node": both_eyes, "both_eyes_translate": [float(cmds_module.getAttr(f"{both_eyes}.translateX")), float(cmds_module.getAttr(f"{both_eyes}.translateY"))]}


def capture_current_look_at_position(character_node: str, *, cmds_module: Any | None = None) -> list[float]:
    """Return world-space debug provenance only, never a gaze translate value."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    node=qualify_rig_control(character_node,"eyeStare_world")
    if not cmds_module.objExists(node): raise RuntimeError(f"Could not resolve eyeStare_world for {character_node}")
    return list(cmds_module.xform(node,query=True,worldSpace=True,translation=True))


def resolve_actor_target_position(alias: str, target: str, character_mappings: dict[str, dict[str, Any]]) -> list[float]:
    targets = (character_mappings.get(alias) or {}).get("gaze_targets") or {}
    value = targets.get(target)
    if not isinstance(value, dict) or not isinstance(value.get("eye_stare_translate"), (list, tuple)) or len(value["eye_stare_translate"]) != 3:
        raise ValueError(f"Character {alias} requires an artist-captured gaze target position for {target}.")
    return [float(item) for item in value["eye_stare_translate"]]


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


def capture_eyelid_animation_reference(node: str, attr: str, *, cmds_module: Any) -> dict[str, Any]:
    """Read JALI's existing eyelid curve without changing it."""
    plug = f"{node}.{attr}"
    if not cmds_module.objExists(plug):
        raise RuntimeError(f"Eyelid attribute does not exist: {plug}")
    times = list(cmds_module.keyframe(node, attribute=attr, query=True, timeChange=True) or [])
    values = list(cmds_module.keyframe(node, attribute=attr, query=True, valueChange=True) or [])
    return {"node": node, "attr": attr, "keys": [{"frame": float(frame), "value": float(value)} for frame, value in zip(times, values)]}


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
        if mode == "RESET":
            state.update({"eye_stare": list(neutral_position), "eyes": list(neutral_eyes)})
        elif mode in {"GAZE","GLANCE"}:
            directions = {"RIGHT", "LEFT", "DOWN", "DOWN_LEFT", "DOWN_RIGHT", "UP", "UP_LEFT", "UP_RIGHT"}
            if target in directions:
                x, y = directional_eye_offset(target, magnitude=magnitude, limit=limit)
                state.update({"eye_stare": list(neutral_position), "eyes": [neutral_eyes[0] + x, neutral_eyes[1] + y]})
            else:
                if target not in target_positions: raise ValueError(f"No artist-captured gaze target position for {target}.")
                state.update({"eye_stare":list(target_positions[target]),"eyes":list(neutral_eyes)})
            if mode=="GLANCE": state["return_state"]=dict(previous)
        else:
            x,y=directional_eye_offset(target,magnitude=magnitude,limit=limit,social=bool(event.get("social_avert")))
            state.update({"eye_stare":list(neutral_position),"eyes":[neutral_eyes[0]+x,neutral_eyes[1]+y]})
        if mode != "GLANCE": previous={"eye_stare":list(state["eye_stare"]),"eyes":list(state["eyes"])}
        schedule.append(state)
    return schedule


def build_dual_gaze_key_schedule(schedule: Iterable[dict[str, Any]], *, fps: float, transition_frames: int = 3, glance_min_hold_seconds: float | None = None, glance_hold_seconds: float | None = None, glance_transition_frames: int | None = None, allow_shortened_glance: bool = False, timeline_start: float = 0.0, initialization_epsilon: float = 1e-6) -> list[dict[str, Any]]:
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
            transition = max(1, int(glance_transition_frames if glance_transition_frames is not None else transition_frames))
            hold_seconds = glance_hold_seconds if glance_hold_seconds is not None else (glance_min_hold_seconds if glance_min_hold_seconds is not None else 0.5)
            minimum_hold = 1 if allow_shortened_glance else max(1, int(math.ceil(float(hold_seconds) * float(fps))))
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
        eye.append({"id": event.get("id") or event.get("phrase_id"), "phrase_id": event.get("phrase_id"), "source_proposal_id": event.get("source_proposal_id"), "reason": event.get("reason"), "type": kind, "value": event.get("value"), "mode": event.get("value"), "resolved_time": dict(resolved_time)})
    return eye


def _validate_gaze_reference(reference: dict[str, Any], *, cmds_module: Any) -> None:
    for key in ("eye_stare_node", "both_eyes_node"):
        node = str(reference.get(key) or "")
        if not node or not cmds_module.objExists(node):
            raise RuntimeError(f"Prepared neutral gaze reference has no existing {key}.")
    if not isinstance(reference.get("eye_stare_translate"), (list, tuple)) or len(reference["eye_stare_translate"]) != 3:
        raise ValueError("Prepared neutral gaze reference requires eye_stare_translate.")
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
        # Old world-space neutral captures are intentionally not reusable.
        reference=capture_character_gaze_reference(node, cmds_module=cmds)
        _validate_gaze_reference(reference, cmds_module=cmds)
        positions: dict[str, list[float]] = {}
        for item in gaze:
            if item["target"] not in {"__BASE__", "DOWN", "UP", "LEFT", "RIGHT", "DOWN_LEFT", "DOWN_RIGHT", "UP_LEFT", "UP_RIGHT"}:
                positions[item["target"]] = resolve_actor_target_position(alias,item["target"],character_mappings)
        schedule=build_dual_gaze_schedule(gaze,neutral_position=reference["eye_stare_translate"],neutral_eyes=reference["both_eyes_translate"],target_positions=positions,magnitude=float(gaze_config.get("directional_eye_magnitude",5)),limit=float(gaze_config.get("directional_eye_limit",6)))
        key_schedule=build_dual_gaze_key_schedule(schedule,fps=float(manifest["fps"]),transition_frames=int(gaze_config.get("gaze_transition_frames",3)),glance_hold_seconds=float(gaze_config.get("glance_hold_seconds", gaze_config.get("glance_min_hold_seconds", 0.5))))
        eye = _dual_eye_events(events)
        eyelid_control = qualify_rig_control(node, str(eye_config.get("eyelid_control_suffix", "LIDS_jSync_plusMinus")))
        eyelid_attr = str(eye_config.get("eyelid_attr", "Down_upLids_jSync"))
        eyelid_reference = capture_eyelid_animation_reference(eyelid_control, eyelid_attr, cmds_module=cmds)
        prepared["jsync_nodes"][alias] = jsync
        prepared[alias] = {"maya_node": node, "jsync_node": jsync, "sound_file": runtime[alias]["sound_file"], "gaze_reference": reference, "gaze_events": gaze, "gaze_schedule": schedule, "gaze_key_schedule": key_schedule, "eye_events": eye, "eyelid_reference": eyelid_reference, "eyelid_control_suffix": eyelid_control, "eyelid_attr": eyelid_attr, "affect_event_count_compiled_not_applied": sum(e.get("channel")=="affect" for e in events), "heart_event_count_compiled_not_applied": sum(e.get("channel")=="heart" for e in events), "head_event_count_compiled_not_applied": sum(e.get("channel")=="head" for e in events), "lid_event_count_compiled_deferred": sum(e.get("channel")=="lid" for e in events)}
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
            frame=state["frame"]
            for axis, value in zip("XYZ", state["eye_stare"]):
                cmds.setAttr(f"{reference['eye_stare_node']}.translate{axis}", value)
                cmds.setKeyframe(reference["eye_stare_node"], attribute=f"translate{axis}", time=frame)
            cmds.setAttr(f"{reference['both_eyes_node']}.translateX",state["eyes"][0]); cmds.setAttr(f"{reference['both_eyes_node']}.translateY",state["eyes"][1]); cmds.setKeyframe(reference["both_eyes_node"],attribute="translateX",time=frame); cmds.setKeyframe(reference["both_eyes_node"],attribute="translateY",time=frame)
        adapter_dir=base_path / "maya_adapter" / alias; adapter_dir.mkdir(parents=True,exist_ok=True)
        gaze_path=adapter_dir / "gaze_events.json"; eye_path=adapter_dir / "eye_events.json"
        gaze_path.write_text(json.dumps({"events":item["gaze_events"],"schedule":item["gaze_schedule"],"key_schedule":item["gaze_key_schedule"]}),encoding="utf-8"); eye_path.write_text(json.dumps({"events":item["eye_events"]}),encoding="utf-8")
        if item["eye_events"]: apply_eye_performance_events(eye_events_path=str(eye_path), fps=fps, eyelid_control_suffix=item["eyelid_control_suffix"], eyelid_attr=item["eyelid_attr"], clear_existing_eyelid_keys=False, preserve_existing_regulatory_blinks=True, apply_lid_states=False, apply_weighted_flat_tangents=False)
        result[alias] = {key: item[key] for key in ("jsync_node", "sound_file", "gaze_reference", "eyelid_reference", "affect_event_count_compiled_not_applied", "heart_event_count_compiled_not_applied", "head_event_count_compiled_not_applied", "lid_event_count_compiled_deferred")}
        result[alias].update({"gaze_event_count":len(item["gaze_events"]), "eye_event_count":len(item["eye_events"]), "warnings":["affect/heart compiled but not yet applied in dual runtime", "head compiled but not yet applied", "dual semantic lid_state compiled but deferred until eyelid layering is implemented"]})
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
