from __future__ import annotations

from pathlib import Path
import json
import sys
from types import SimpleNamespace

import pytest


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from animation_apply_runner import (  # noqa: E402
    apply_animation_artifacts,
    build_explicit_target_map,
    qualify_rig_control,
    resolve_jsync_for_character,
    scene_fps_from_unit,
    validate_gaze_target_mappings,
)


def test_scene_fps_supports_named_and_numeric_maya_units():
    assert scene_fps_from_unit("film") == 24.0
    assert scene_fps_from_unit("ntsc") == 30.0
    assert scene_fps_from_unit("23.976fps") == 23.976
    with pytest.raises(ValueError, match="Unsupported Maya time unit"):
        scene_fps_from_unit("mystery")


def test_explicit_target_map_uses_ui_nodes_and_always_supports_base_reset():
    target_map = build_explicit_target_map(
        [{"semantic_target": "CRYSTAL", "maya_node": "|props|crystal_LOC"}]
    )
    assert target_map["CRYSTAL"] == {"node": "|props|crystal_LOC"}
    assert target_map["__BASE__"] == {"offset": [0.0, 0.0, 0.0]}


def test_incomplete_look_at_mapping_is_rejected():
    with pytest.raises(ValueError, match="requires both"):
        build_explicit_target_map([{"semantic_target": "", "maya_node": "|crystal_LOC"}])
    assert "CRYSTAL" not in build_explicit_target_map(
        [{"semantic_target": "CRYSTAL", "maya_node": ""}]
    )


def test_animation_preflight_requires_object_mapping_but_accepts_semantic_name_alias():
    events = [{"target": "OBJECT_HAWK", "resolved_time": {"start": 0.0, "end": 1.0}}]
    with pytest.raises(ValueError, match="Missing Maya look-at mapping for semantic target HAWK"):
        validate_gaze_target_mappings(
            events,
            target_map=build_explicit_target_map([]),
            configured_directions={"DOWN"},
        )
    target_map = validate_gaze_target_mappings(
        events,
        target_map=build_explicit_target_map([{"semantic_target": "HAWK", "maya_node": "|hawk_LOC"}]),
        configured_directions={"DOWN"},
    )
    assert target_map["OBJECT_HAWK"] == {"node": "|hawk_LOC"}


def test_active_character_namespace_qualifies_rig_controls():
    assert qualify_rig_control("|world|auntEm:ROOT", "eyeStare_world") == "auntEm:eyeStare_world"
    assert qualify_rig_control("|world|ROOT", "eyeStare_world") == "eyeStare_world"
    assert qualify_rig_control("|world|auntEm:ROOT", "custom:jSync1") == "custom:jSync1"


class _JSyncCmds:
    def __init__(self, nodes, sounds=None):
        self.nodes, self.sounds = nodes, sounds or {}

    def ls(self, **kwargs):
        assert kwargs == {"type": "jSync", "long": True}
        return self.nodes

    def getAttr(self, attribute):
        return self.sounds[attribute.rsplit(".", 1)[0]]


def test_resolve_jsync_uses_character_dag_not_leaf_name_or_namespace():
    cmds = _JSyncCmds([
        "|world|ValleyGirl_jRigMaya:JALI_GRP|speechMaster|jSync1_parent|jSync1",
        "|world|Angela_jRigMaya:JALI_GRP|speechMaster|jSync2_parent|jSync2",
    ])
    assert resolve_jsync_for_character("|world|ValleyGirl_jRigMaya:JALI_GRP", cmds_module=cmds).endswith("|jSync1")
    assert resolve_jsync_for_character("|world|Angela_jRigMaya:JALI_GRP", cmds_module=cmds).endswith("|jSync2")


def test_resolve_jsync_disambiguates_sound_file_and_reports_missing_or_ambiguous():
    root = "|world|ValleyGirl_jRigMaya:JALI_GRP"
    one, two = root + "|a|jSync1", root + "|b|jSync3"
    cmds = _JSyncCmds([one, two], {one: "SeqT_AGNES", two: "other"})
    assert resolve_jsync_for_character(root, "SeqT_AGNES", cmds_module=cmds) == one
    with pytest.raises(RuntimeError, match="No jSync node"):
        resolve_jsync_for_character("|world|Missing", cmds_module=cmds)
    with pytest.raises(RuntimeError, match="Ambiguous jSync"):
        resolve_jsync_for_character(root, cmds_module=cmds)


def test_apply_uses_explicit_manifest_paths_and_ui_mapping(monkeypatch, tmp_path: Path):
    import expregaze_jali.maya_apply_eye_performance as eye_module
    import expregaze_jali.maya_apply_gaze as gaze_module
    import expregaze_jali.maya_apply_jali_annotation as jali_module

    fake_cmds = SimpleNamespace(
        objExists=lambda _node: True,
        ls=lambda **kwargs: ["|world|auntEm:ROOT|speechMaster|jSync1_parent|jSync1"],
    )
    monkeypatch.setitem(sys.modules, "maya", SimpleNamespace(cmds=fake_cmds))
    calls: dict[str, dict] = {}
    monkeypatch.setattr(jali_module, "load_jali_annotation_config", lambda _path: {})
    monkeypatch.setattr(gaze_module, "load_maya_gaze_config", lambda _path: {
        "direction_offsets": {"DOWN": [0, -25, 0]}
    })
    monkeypatch.setattr(eye_module, "load_maya_eye_config", lambda _path: {})
    monkeypatch.setattr(
        jali_module, "apply_jali_annotation", lambda **kwargs: calls.setdefault("jali", kwargs)
    )
    monkeypatch.setattr(
        gaze_module, "apply_gaze_events", lambda **kwargs: calls.setdefault("gaze", kwargs)
    )
    monkeypatch.setattr(
        eye_module,
        "apply_eye_performance_events",
        lambda **kwargs: calls.setdefault("eye", kwargs),
    )

    artifacts = {
        "annotated_for_jali": tmp_path / "annotated_for_jali.txt",
        "gaze_events": tmp_path / "gaze_events_resolved.json",
        "eye_performance_events": tmp_path / "eye_performance_events.json",
        "head_events": tmp_path / "head_events_resolved.json",
        "runtime_transcript": tmp_path / "jali_runtime_transcript.txt",
    }
    artifacts["annotated_for_jali"].write_text("<mask=Friendly-50> Hi </mask=Friendly-50>", encoding="utf-8")
    artifacts["runtime_transcript"].write_text("Hi", encoding="utf-8")
    artifacts["gaze_events"].write_text(
        json.dumps({
            "events": [{
                "type": "gaze",
                "mode": "GAZE",
                "target": "CRYSTAL",
                "resolved_time": {"start": 0.0, "end": 1.0},
            }]
        }),
        encoding="utf-8",
    )
    artifacts["eye_performance_events"].write_text(
        json.dumps({
            "lid_state_events": [{"resolved_time": {"start": 0.0, "end": 1.0}}],
            "performative_blink_events": [],
            "regulatory_blink_events": [],
            "blink_suppression_events": [],
        }),
        encoding="utf-8",
    )
    artifacts["head_events"].write_text(json.dumps({"events": []}), encoding="utf-8")
    manifest = tmp_path / "animation_manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": "hci_animation_manifest_v0",
            "fps": 24.0,
            "clip_end_frame": 24.0,
            "artifacts": {key: str(path) for key, path in artifacts.items()},
        }),
        encoding="utf-8",
    )
    config = tmp_path / "maya.yaml"
    config.write_text("test: true\n", encoding="utf-8")

    result = apply_animation_artifacts(
        manifest_path=manifest,
        active_character_node="|world|auntEm:ROOT",
        look_at_mappings=[{"semantic_target": "CRYSTAL", "maya_node": "|crystal_LOC"}],
        maya_config_path=config,
    )

    assert calls["jali"]["annotated_for_jali_path"] == str(artifacts["annotated_for_jali"])
    assert calls["jali"]["jsync_node"] == "|world|auntEm:ROOT|speechMaster|jSync1_parent|jSync1"
    assert calls["gaze"]["target_map"]["CRYSTAL"] == {"node": "|crystal_LOC"}
    assert calls["gaze"]["eye_stare_node_suffix"] == "auntEm:eyeStare_world"
    assert calls["eye"]["eyelid_control_suffix"] == "auntEm:LIDS_jSync_plusMinus"
    assert result["jali_applied"] is True
