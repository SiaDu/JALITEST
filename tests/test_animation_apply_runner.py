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
    adapt_dual_gaze_events,
    directional_eye_offset,
    build_dual_gaze_schedule,
    build_dual_gaze_key_schedule,
    clear_character_gaze_animation,
    prepare_dual_animation_overlay,
    apply_dual_animation_artifacts,
    apply_dual_speaker_emotion_artifacts,
    apply_dual_listener_mask_artifacts,
    build_listener_mask_key_schedule,
    build_listener_mask_timeline,
    capture_character_gaze_reference,
    capture_eyelid_animation_reference,
    prepare_dual_listener_mask_artifacts,
    resolve_character_look_at_target,
    resolve_jsync_for_character,
    resolve_jali_source_transcript_path,
    scene_fps_from_unit,
    validate_gaze_target_mappings,
    apply_dual_gaze_only_artifacts,
    prepare_dual_gaze_only_artifacts,
    gaze_layer_name,
    head_layer_name,
    blink_layer_name,
    build_head_overlay_key_schedule,
    build_blink_overlay_key_schedule,
    apply_dual_v2_head_blink_overlays,
    prepare_dual_v2_head_blink_overlays,
    plan_v2_blinks,
    diagnose_v2_blink_ownership,
)
from listener_mask_library import AU_TO_USER_CONTROL, FACTORY_MASK_AUS, user_pose_for_mask  # noqa: E402


def test_resolve_jali_source_transcript_path_supports_directory_and_full_txt(tmp_path):
    a = tmp_path / "SeqT_AGNES.txt"; a.write_text("one", encoding="utf-8")
    b_dir = tmp_path / "other"; b_dir.mkdir(); b = b_dir / "SeqT_WILL.txt"; b.write_text("two", encoding="utf-8")
    assert resolve_jali_source_transcript_path(tmp_path, "SeqT_AGNES") == a.resolve()
    assert resolve_jali_source_transcript_path(b, "SeqT_WILL") == b.resolve()
    with pytest.raises(FileNotFoundError, match="JALI source transcript not found"):
        resolve_jali_source_transcript_path(tmp_path, "MISSING")


def test_dual_emotion_preflight_failure_mutates_neither_actor(monkeypatch, tmp_path):
    import animation_apply_runner as runner
    artifacts = {}
    for alias in ("A", "B"):
        event = tmp_path / f"{alias}.json"; event.write_text('{"events": []}'); artifacts[alias] = str(event)
        text = tmp_path / f"{alias}.txt"; text.write_text("hello"); artifacts[f"{alias}_jali_speaker_annotated"] = str(text)
        diag = tmp_path / f"{alias}_diag.json"; diag.write_text(json.dumps({"alias":alias,"script_name":"AGNES" if alias == "A" else "WILL","mask_tag_count":0,"heart_tag_count":0})); artifacts[f"{alias}_jali_speaker_annotation"] = str(diag)
    wavs={}
    for alias in ("A","B"):
        wav=tmp_path/f"{alias}.wav"; wav.write_bytes(b"wav"); wavs[alias]={"path":str(wav)}
    manifest=tmp_path/"manifest.json"; manifest.write_text(json.dumps({"schema_version":"dual_animation_manifest_v0","character_runtime_mapping":{"A":{"script_name":"AGNES","sound_file":"SA"},"B":{"script_name":"WILL","sound_file":"SB"}},"wav_durations":wavs,"artifacts":artifacts}))
    class Cmds:
        calls=[]
        def objExists(self, plug): return "jSync2.calculate_expression" not in plug
        def getAttr(self, plug): return "SA" if "jSync1" in plug else "SB"
        def attributeQuery(self, *_a, **_k): return ["from Annotation:From Transcript Tags"]
        def setAttr(self,*a,**k): self.calls.append((a,k))
    cmds=Cmds(); mel=SimpleNamespace(eval=lambda value: 1 if "exists" in value else pytest.fail("MEL must not run"))
    monkeypatch.setattr(runner,"resolve_jsync_for_character",lambda rig,*_a,**_k: "|A:ROOT|jSync1" if rig == "|A:ROOT" else "|B:ROOT|jSync2")
    with pytest.raises(RuntimeError,match="B: jSync is missing"):
        apply_dual_speaker_emotion_artifacts(manifest_path=manifest,character_mappings={"A":{"maya_node":"|A:ROOT"},"B":{"maya_node":"|B:ROOT"}},cmds_module=cmds,mel_module=mel)
    assert cmds.calls == []


def test_dual_emotion_realigns_from_separate_staging_and_restores_paths(monkeypatch, tmp_path):
    import animation_apply_runner as runner
    artifacts={}; wavs={}
    for alias, stem in (("A","SA"),("B","SB")):
        event=tmp_path/f"{alias}.json"; event.write_text('{"events": []}'); artifacts[alias]=str(event)
        text=tmp_path/f"{alias}_tagged.txt"; text.write_text(f"<mask=Polite-20> {alias} </mask=Polite-20>"); artifacts[f"{alias}_jali_speaker_annotated"]=str(text)
        diag=tmp_path/f"{alias}_diag.json"; diag.write_text(json.dumps({"alias":alias,"script_name":alias,"mask_tag_count":1,"heart_tag_count":0})); artifacts[f"{alias}_jali_speaker_annotation"]=str(diag)
        wav=tmp_path/f"{stem}.wav"; wav.write_bytes(alias.encode()); wavs[alias]={"path":str(wav)}
    manifest=tmp_path/"manifest.json"; manifest.write_text(json.dumps({"schema_version":"dual_animation_manifest_v0","character_runtime_mapping":{"A":{"script_name":"A","sound_file":"SA"},"B":{"script_name":"B","sound_file":"SB"}},"wav_durations":wavs,"artifacts":artifacts}))
    class Cmds:
        def __init__(self): self.values={}; self.calls=[]; self.selection=[]
        def objExists(self,_): return True
        def getAttr(self,p): return self.values.get(p, "SA" if "jSync1.sound_file" in p else "SB" if "jSync2.sound_file" in p else "original/")
        def attributeQuery(self,*_a,**_k): return ["from Annotation:From Transcript Tags"]
        def setAttr(self,*a,**k): self.values[a[0]]=a[1]; self.calls.append(a)
        def ls(self,**k): return self.selection if k.get("selection") else []
        def select(self,items=None,**k): self.selection=[] if k.get("clear") else list(items or [])
    cmds=Cmds(); mel_calls=[]; mel=SimpleNamespace(eval=lambda value: mel_calls.append(value) or (1 if "exists" in value else None))
    monkeypatch.setattr(runner,"resolve_jsync_for_character",lambda rig,*_a,**_k: "|A:ROOT|jSync1" if rig == "|A:ROOT" else "|B:ROOT|jSync2")
    result=apply_dual_speaker_emotion_artifacts(manifest_path=manifest,character_mappings={"A":{"maya_node":"|A:ROOT"},"B":{"maya_node":"|B:ROOT"}},cmds_module=cmds,mel_module=mel)
    assert all(Path(result[a]["staging_txt"]).read_text().startswith("<mask=") for a in ("A","B"))
    assert Path(result["A"]["staging_wav"]).read_bytes() == b"A" and Path(result["B"]["staging_wav"]).read_bytes() == b"B"
    assert all(result[a]["paths_restored"] for a in ("A","B")) and sum(call.startswith('realign_node ') for call in mel_calls) == 2


def test_v2_generate_disables_jali_blink_before_each_realign(monkeypatch, tmp_path):
    import animation_apply_runner as runner
    artifacts = {"characters": {}}
    wavs = {}
    runtime = {}
    for actor, stem in (("ALICE", "SA"), ("BOB", "SB")):
        events = tmp_path / f"{actor}_events.json"; events.write_text('{"events": []}')
        text = tmp_path / f"{actor}.txt"; text.write_text(actor)
        diagnostic = tmp_path / f"{actor}_diag.json"; diagnostic.write_text(json.dumps({"actor": actor, "script_name": actor, "mask_tag_count": 0}))
        artifacts["characters"][actor] = {"resolved_sparse_events": str(events), "jali_speaker_annotated": str(text), "jali_speaker_annotation": str(diagnostic)}
        wav = tmp_path / f"{stem}.wav"; wav.write_bytes(b"wav")
        wavs[actor] = {"path": str(wav)}
        runtime[actor] = {"script_name": actor, "sound_file": stem}
    timing = tmp_path / "timing.json"; timing.write_text("{}")
    artifacts["conversation_anchor_timing"] = str(timing)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "dual_animation_manifest_v2", "characters": ["ALICE", "BOB"], "fps": 24, "character_runtime_mapping": runtime, "wav_durations": wavs, "artifacts": artifacts}))
    log = []
    class Cmds:
        def __init__(self): self.values = {}
        def objExists(self, _plug): return True
        def getAttr(self, plug):
            if plug.endswith(".sound_file"): return "SA" if "ALICE" in plug else "SB"
            if plug.endswith(".calculate_blinks"): return self.values.get(plug, True)
            return self.values.get(plug, "original/")
        def setAttr(self, plug, value, **_kwargs): self.values[plug] = value; log.append(("set", plug, value))
        def ls(self, **_kwargs): return []
        def select(self, *_args, **_kwargs): pass
    cmds = Cmds()
    def mel_eval(value):
        log.append(("mel", value))
        return 1 if "exists" in value else None
    monkeypatch.setattr(runner, "resolve_jsync_for_character", lambda rig, *_a, **_k: rig + "|" + ("ALICE_jSync" if "ALICE" in rig else "BOB_jSync"))
    result = apply_dual_speaker_emotion_artifacts(manifest_path=manifest, character_mappings={"ALICE": {"maya_node": "|ALICE:ROOT"}, "BOB": {"maya_node": "|BOB:ROOT"}}, cmds_module=cmds, mel_module=SimpleNamespace(eval=mel_eval))
    for actor in ("ALICE", "BOB"):
        plug = f"|{actor}:ROOT|{actor}_jSync.calculate_blinks"
        disable_index = log.index(("set", plug, False))
        realign_index = next(index for index, row in enumerate(log) if row[0] == "mel" and "realign_node" in row[1] and actor in row[1])
        assert disable_index < realign_index
        assert result[actor]["calculate_blinks"] is False


def test_confused_25_scales_factory_coefficients_and_filters_eyelids():
    pose = user_pose_for_mask("Confused-25")
    assert pose["usr_InnerBrowRaise_L.InnerBrowRaise_L"] == 1.25
    assert pose["usr_OuterBrowRaise_R.OuterBrowRaise_R"] == 1.5
    assert pose["usr_BrowInDown_L.BrowIn_L"] == 1.875
    assert pose["usr_Wince_R.Wince_R"] == 0.5
    assert pose["usr_Pucker_L.Pucker_L"] == 0.5
    assert pose["usr_Squint_R.Squint_R"] == 0.5
    assert "au05_uLidUpL" in FACTORY_MASK_AUS["Confused"]
    assert all("blink" not in plug.casefold() and "lid" not in plug.casefold() for plug in pose)


def test_listener_timeline_assigns_affect_only_to_non_speaker_and_updates_intensity():
    phrases = [
        {"phrase_id": "P01", "speaker": "A", "canonical_start": 0, "canonical_end": 1},
        {"phrase_id": "P02", "speaker": "A", "canonical_start": 1, "canonical_end": 2},
        {"phrase_id": "P03", "speaker": "B", "canonical_start": 2, "canonical_end": 3},
    ]
    timeline = build_listener_mask_timeline(phrases, events_by_actor={
        "A": [{"phrase_id": "P03", "channel": "affect", "value": "Smug-20"}],
        "B": [{"phrase_id": "P01", "channel": "affect", "value": "Watchful-25"}, {"phrase_id": "P02", "channel": "affect", "value": "Watchful-35"}],
    })
    assert [item["state"] for item in timeline["A"]] == ["NONE", "NONE", "Smug-20"]
    assert [item["state"] for item in timeline["B"]] == ["Watchful-25", "Watchful-35", "NONE"]
    keys = build_listener_mask_key_schedule(timeline["B"], fps=24)
    assert [key["frame"] for key in keys] == [0.0, 22.0, 26.0, 46.0, 50.0]
    assert keys[2]["pose"]["usr_OuterBrowRaise_L.OuterBrowRaise_L"] == 1.75


class _ListenerCmds:
    def __init__(self, *, missing: str = ""):
        self.calls = []; self.layers = set(); self.missing = missing
    def objExists(self, plug): return plug != self.missing
    def attributeQuery(self, *_args, **_kwargs): return ["User:Jali:Add"]
    def playbackOptions(self, **kwargs): return 0.0 if kwargs.get("minTime") else 240.0
    def animLayer(self, layer, **kwargs):
        self.calls.append(("animLayer", (layer,), kwargs))
        if not kwargs: self.layers.add(layer)
        return [] if kwargs.get("query") else layer
    def cutKey(self, *args, **kwargs): self.calls.append(("cutKey", args, kwargs))
    def setAttr(self, *args, **kwargs): self.calls.append(("setAttr", args, kwargs))
    def setKeyframe(self, *args, **kwargs): self.calls.append(("setKeyframe", args, kwargs))


def _listener_manifest(tmp_path: Path, *, b_affect: str = "Confused-25") -> Path:
    artifacts = {}
    for alias, events in (("A", [{"phrase_id": "P02", "channel": "affect", "value": "Smug-20"}]), ("B", [{"phrase_id": "P01", "channel": "affect", "value": b_affect}])):
        path = tmp_path / f"{alias}_events.json"; path.write_text(json.dumps({"events": events})); artifacts[alias] = str(path)
    timing = tmp_path / "conversation_phrase_timing.json"
    timing.write_text(json.dumps({"phrases": [{"phrase_id": "P01", "speaker": "A", "canonical_start": 0, "canonical_end": 1}, {"phrase_id": "P02", "speaker": "B", "canonical_start": 1, "canonical_end": 2}]}))
    artifacts["conversation_phrase_timing"] = str(timing)
    manifest = tmp_path / "listener_manifest.json"
    manifest.write_text(json.dumps({"schema_version": "dual_animation_manifest_v0", "fps": 24.0, "character_runtime_mapping": {"A": {"script_name": "AGNES", "sound_file": "A"}, "B": {"script_name": "WILL", "sound_file": "B"}}, "artifacts": artifacts}))
    return manifest


def _listener_mappings():
    return {"A": {"maya_node": "|A:ROOT"}, "B": {"maya_node": "|B:ROOT"}}


def test_listener_preflight_and_apply_only_write_user_mask_controls(monkeypatch, tmp_path):
    cmds = _ListenerCmds()
    manifest = _listener_manifest(tmp_path)
    prepared = prepare_dual_listener_mask_artifacts(manifest_path=manifest, character_mappings=_listener_mappings(), cmds_module=cmds)
    assert prepared["A"]["add_index"] == 2 and prepared["B"]["listener_mask_events"] == 1
    result = apply_dual_listener_mask_artifacts(prepared_context=prepared, cmds_module=cmds)
    set_attrs = [call[1][0] for call in cmds.calls if call[0] == "setAttr"]
    assert "A:FACSMaster.FACS_animationSource" in set_attrs and "B:FACSMaster.FACS_animationSource" in set_attrs
    assert not any("jSync" in plug or "blink" in plug.casefold() or "loLid" in plug for plug in set_attrs)
    assert all(plug.startswith(("A:usr_", "B:usr_", "A:FACSMaster.", "B:FACSMaster.")) for plug in set_attrs)
    keyed = [call[1][0] for call in cmds.calls if call[0] == "setKeyframe"]
    assert keyed and all(plug.startswith(("A:usr_", "B:usr_")) for plug in keyed)
    assert result["B"]["eyelid_channels_filtered"] is True and result["A"]["FACS_animationSource"] == "Add"
    assert not any(call[0] == "animLayer" and call[2].get("override") is True for call in cmds.calls)


def test_listener_unsupported_mask_fails_preflight_before_either_actor_mutates(tmp_path):
    cmds = _ListenerCmds()
    with pytest.raises(ValueError, match="Unsupported listener Mask"):
        prepare_dual_listener_mask_artifacts(manifest_path=_listener_manifest(tmp_path, b_affect="Alien-25"), character_mappings=_listener_mappings(), cmds_module=cmds)
    assert cmds.calls == []


def test_listener_missing_b_control_fails_before_either_actor_mutates(tmp_path):
    cmds = _ListenerCmds(missing="B:usr_Squint_R.Squint_R")
    with pytest.raises(RuntimeError, match="B: missing User FACS controls"):
        prepare_dual_listener_mask_artifacts(manifest_path=_listener_manifest(tmp_path), character_mappings=_listener_mappings(), cmds_module=cmds)
    assert cmds.calls == []


def test_v2_head_schedule_is_additive_config_driven_and_none_returns_zero():
    config = {"transition_frames": 4, "strength_degrees": {"SUBTLE": 3, "MEDIUM": 6, "STRONG": 10}, "pitch_axis": "rotateX", "roll_axis": "rotateZ", "pitch_up_sign": -1, "tilt_left_sign": 1}
    events = [
        {"event_id": "E1", "resolved_start": 1.0, "changes": {"head": "HEAD-UP-STRONG"}},
        {"event_id": "E2", "resolved_start": 2.0, "changes": {"head": "HEAD-NONE"}},
    ]
    keys = build_head_overlay_key_schedule(events, fps=24, config=config)
    assert keys[1] == {"frame": 24.0, "values": {"rotateX": -10.0, "rotateY": 0.0, "rotateZ": 0.0}, "event_id": "E1"}
    assert keys[-1]["values"] == {"rotateX": 0.0, "rotateY": 0.0, "rotateZ": 0.0}


def test_v2_blink_schedule_contains_only_explicit_performative_events():
    config = {"open_value": 0, "closed_value": 1, "presets": {"DOUBLE_BLINK": {"close_frames": 2, "hold_frames": 1, "open_frames": 2, "count": 2, "gap_frames": 4}}}
    keys = build_blink_overlay_key_schedule([{"event_id": "E1", "resolved_start": 1.0, "changes": {"blink": "DOUBLE_BLINK"}}], fps=24, config=config)
    assert len(keys) == 8 and keys[0]["frame"] == 24 and keys[-1]["value"] == 0


def _resolved(event_id, time, actor="ALICE", **changes):
    return {"event_id": event_id, "actor": actor, "resolved_start": time, "changes": changes}


def test_v2_regulatory_blink_planner_priority_and_transition_rules():
    events = [
        _resolved("E1", 0, gaze="GAZE-BOB", affect="Watchful-80"),
        _resolved("E2", 1, affect="Watchful-100"),
        _resolved("E3", 2, affect="Nervous-60"),
        _resolved("E4", 3, gaze="GLANCE-DOWN", affect="Happy-60"),
        _resolved("E5", 4, gaze="GAZE-BOB", blink="SLOW_BLINK"),
        _resolved("E6", 5, head="HEAD-UP-SUBTLE", lid=2),
        _resolved("E7", 6, gaze="GAZE-BOB"),
    ]
    planned = plan_v2_blinks(events)
    assert [(row["resolved_start"], row["changes"]["blink"], row["blink_source"]) for row in planned] == [
        (2.0, "BLINK", "affect_regulatory"),
        (3.0, "BLINK", "gaze_regulatory"),
        (4.0, "SLOW_BLINK", "explicit"),
    ]
    assert all(row["resolved_start"] != 1 for row in planned)  # intensity only
    assert sum(row["resolved_start"] == 3 for row in planned) == 1  # gaze beats affect
    assert sum(row["resolved_start"] == 4 for row in planned) == 1  # explicit suppresses regulatory
    assert all(row["resolved_start"] != 6 for row in planned)  # unchanged gaze


def test_v2_regulatory_blink_planner_is_actor_independent_and_first_state_is_initialization():
    alice = plan_v2_blinks([_resolved("A1", 0, gaze="GAZE-BOB"), _resolved("A2", 1, gaze="GAZE-DOWN")])
    bob = plan_v2_blinks([_resolved("B1", 0, actor="BOB", affect="Nervous-60")])
    assert len(alice) == 1 and alice[0]["actor"] == "ALICE"
    assert bob == []
    glance = plan_v2_blinks([_resolved("G1", 0, gaze="GAZE-BOB"), _resolved("G2", 1, gaze="GLANCE-DOWN"), _resolved("G3", 2, gaze="GAZE-BOB")])
    assert [(row["resolved_start"], row["blink_source"]) for row in glance] == [(1.0, "gaze_regulatory")]


def test_v2_overlay_apply_uses_owned_additive_layers_and_user_blink_only():
    cmds = _ListenerCmds()
    context = {"schema_version": "dual_v2_head_blink_prepared_v1", "actors": {
        "ALICE": {
            "head_layer": head_layer_name("ALICE"), "blink_layer": blink_layer_name("ALICE"),
            "head_plugs": ["ALICE:jNeck_ctl.rotateX", "ALICE:jNeck_ctl.rotateY", "ALICE:jNeck_ctl.rotateZ"],
            "blink_plugs": ["ALICE:usr_blink.LidDown"], "facs_plug": "ALICE:FACSMaster.FACS_animationSource", "facs_add_index": 2,
            "head_keys": [{"frame": 10, "values": {"rotateX": 3, "rotateY": 0, "rotateZ": 0}}],
            "blink_keys": [{"frame": 12, "value": 1}],
        },
        "BOB": {
            "head_layer": head_layer_name("BOB"), "blink_layer": blink_layer_name("BOB"),
            "head_plugs": ["BOB:jNeck_ctl.rotateX", "BOB:jNeck_ctl.rotateY", "BOB:jNeck_ctl.rotateZ"],
            "blink_plugs": [], "facs_plug": "BOB:FACSMaster.FACS_animationSource", "facs_add_index": None,
            "head_keys": [], "blink_keys": [],
        },
    }}
    result = apply_dual_v2_head_blink_overlays(prepared_context=context, cmds_module=cmds)
    layer_calls = [call for call in cmds.calls if call[0] == "animLayer"]
    assert layer_calls and not any(call[2].get("override") is True for call in layer_calls)
    keyed = [call for call in cmds.calls if call[0] == "setKeyframe"]
    assert all("jNeck_ctl" in call[1][0] or "usr_blink" in call[1][0] for call in keyed)
    assert result["ALICE"]["jali_calculate_blinks_disabled"] is True


def test_v2_blink_ownership_diagnostic_checks_vendor_and_owned_curves():
    context = {"schema_version": "dual_v2_head_blink_prepared_v1", "actors": {"ALICE": {
        "jsync": "ALICE:jSync", "blink_layer": "JALITEST_blink_ALICE",
        "vendor_blink_plug": "ALICE:LIDS_jSync_plusMinus.Down_upLids_jSync",
        "blink_plugs": ["ALICE:usr_blink.LidDown"],
    }}}
    class Cmds:
        def __init__(self, bad=False): self.bad = bad
        def getAttr(self, _plug): return self.bad
        def objExists(self, _node): return True
        def animLayer(self, _layer, **_kwargs): return ["jalitestBlinkCurve"]
        def listConnections(self, plug, **_kwargs):
            if "LIDS_jSync" in plug: return ["vendorBlinkCurve"] if self.bad else []
            return ["foreignCurve"] if self.bad else ["jalitestBlinkCurve"]
    assert diagnose_v2_blink_ownership(prepared_context=context, cmds_module=Cmds())["passed"] is True
    report = diagnose_v2_blink_ownership(prepared_context=context, cmds_module=Cmds(True), strict=False)
    assert report["passed"] is False and report["actors"]["ALICE"]["vendor_anim_curves"] == ["vendorBlinkCurve"]
    with pytest.raises(RuntimeError, match="calculate_blinks is not False.*vendor blink output"):
        diagnose_v2_blink_ownership(prepared_context=context, cmds_module=Cmds(True))


def test_v2_actor_two_blink_preflight_fails_before_any_maya_mutation(monkeypatch, tmp_path):
    import animation_apply_runner as runner
    artifacts = {"characters": {}}
    for actor in ("ALICE", "BOB"):
        events = tmp_path / f"{actor}_events.json"
        events.write_text(json.dumps({"events": [{"event_id": f"E_{actor}", "resolved_start": 1, "changes": {"blink": "BLINK"}}]}))
        annotated = tmp_path / f"{actor}.txt"; annotated.write_text("hello")
        diagnostic = tmp_path / f"{actor}_annotation.json"; diagnostic.write_text("{}")
        artifacts["characters"][actor] = {"resolved_sparse_events": str(events), "jali_speaker_annotated": str(annotated), "jali_speaker_annotation": str(diagnostic)}
    timing = tmp_path / "timing.json"; timing.write_text("{}")
    artifacts["conversation_anchor_timing"] = str(timing)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "dual_animation_manifest_v2", "characters": ["ALICE", "BOB"], "fps": 24, "shared_duration_seconds": 2, "character_runtime_mapping": {"ALICE": {"script_name": "ALICE", "sound_file": "A"}, "BOB": {"script_name": "BOB", "sound_file": "B"}}, "artifacts": artifacts}))
    mappings = {"ALICE": {"script_name": "ALICE", "maya_node": "|ALICE:ROOT"}, "BOB": {"script_name": "BOB", "maya_node": "|BOB:ROOT"}}
    class Cmds(_ListenerCmds):
        def objExists(self, plug):
            if "BOB:usr_blink" in plug:
                return False
            return True
    cmds = Cmds()
    monkeypatch.setattr(runner, "_validate_dual_jali_base", lambda *_a, **_k: {actor: {} for actor in mappings})
    monkeypatch.setattr(runner, "resolve_jsync_for_character", lambda rig, *_a, **_k: rig + "|jSync1")
    with pytest.raises(RuntimeError, match="BOB.*no usable User blink control"):
        prepare_dual_v2_head_blink_overlays(manifest_path=manifest, character_mappings=mappings, baseline={}, cmds_module=cmds, mel_module=SimpleNamespace())
    assert cmds.calls == []


def test_gaze_only_uses_dedicated_layers_and_never_clears_base_controls():
    cmds = _ListenerCmds()
    context = {"schema_version": "dual_gaze_only_prepared_v1", "fps": 24, "jsync_nodes": {"A": "a", "B": "b"}}
    for alias in ("A", "B"):
        context[alias] = {"layer": gaze_layer_name(alias), "managed_gaze_plugs": [f"{alias}:eye.translateX", f"{alias}:eye.translateY", f"{alias}:eye.translateZ", f"{alias}:eyes.translateX", f"{alias}:eyes.translateY"], "reference": {"eye_stare_node": f"{alias}:eye", "both_eyes_node": f"{alias}:eyes"}, "keys": [{"frame": 1.0, "eye_stare": [1,2,3], "eyes": [4,5]}], "gaze_events": 1}
    result = apply_dual_gaze_only_artifacts(prepared_context=context, cmds_module=cmds)
    assert result["A"]["layer"] == "JALITEST_gaze_A"
    assert all(call[0] != "xform" for call in cmds.calls)
    keyed = [call for call in cmds.calls if call[0] == "setKeyframe"]
    assert keyed and all(call[2]["animLayer"].startswith("JALITEST_gaze_") for call in keyed)
    override_calls = [call for call in cmds.calls if call[0] == "animLayer" and call[2].get("override") is True]
    assert {call[1][0] for call in override_calls} == {"JALITEST_gaze_A", "JALITEST_gaze_B"}


def test_named_gaze_apply_uses_prepared_actor_contexts_not_legacy_aliases():
    cmds = _ListenerCmds()
    context = {"schema_version": "dual_gaze_only_prepared_v1", "fps": 24, "jsync_nodes": {"ALICE": "aliceSync", "BOB": "bobSync"}}
    for actor in ("ALICE", "BOB"):
        context[actor] = {
            "layer": gaze_layer_name(actor),
            "managed_gaze_plugs": [f"{actor}:eye.translateX", f"{actor}:eye.translateY", f"{actor}:eye.translateZ", f"{actor}:eyes.translateX", f"{actor}:eyes.translateY"],
            "reference": {"eye_stare_node": f"{actor}:eye", "both_eyes_node": f"{actor}:eyes"},
            "keys": [{"frame": 1.0, "eye_stare": [1, 2, 3], "eyes": [4, 5]}],
            "gaze_events": 1,
        }
    result = apply_dual_gaze_only_artifacts(prepared_context=context, cmds_module=cmds)
    assert set(result) == {"ALICE", "BOB"}
    assert {call[1][0] for call in cmds.calls if call[0] == "animLayer" and call[2].get("override") is True} == {"JALITEST_gaze_ALICE", "JALITEST_gaze_BOB"}


def test_dual_gaze_neutral_is_automatic_local_xy_zero_and_preserves_z_baseline():
    cmds = _DualCmds()
    reference = capture_character_gaze_reference("|A:ROOT", cmds_module=cmds)
    assert reference["eye_stare_translate"] == [0.0, 0.0, 9.0]
    assert reference["baseline_translateZ"] == 9.0
    assert reference["both_eyes_translate"] == [1.0, 2.0]
    assert not any(call[0] == "xform" for call in cmds.calls)


def test_dual_gaze_uses_local_target_pose_not_world_provenance(monkeypatch, tmp_path):
    cmds = _DualCmds(); _patch_dual_runtime(monkeypatch, cmds)
    prepared = prepare_dual_gaze_only_artifacts(
        manifest_path=_dual_manifest(tmp_path), character_mappings=_dual_mappings(), cmds_module=cmds
    )
    assert prepared["A"]["schedule"][0]["eye_stare"] == [1.0, 2.0, 3.0]
    apply_dual_gaze_only_artifacts(prepared_context=prepared, cmds_module=cmds)
    local_keys = [call for call in cmds.calls if call[0] == "setKeyframe" and call[1][0] == "A:eyeStare_world"]
    assert local_keys and {call[2]["value"] for call in local_keys} <= {1.0, 2.0, 3.0}
    assert all(call[2]["animLayer"] == "JALITEST_gaze_A" for call in local_keys)


def test_legacy_world_only_target_calibration_must_be_recaptured(monkeypatch, tmp_path):
    cmds = _DualCmds(); _patch_dual_runtime(monkeypatch, cmds)
    mappings = _dual_mappings()
    mappings["A"]["gaze_targets"]["B"] = [10.0, 20.0, 30.0]
    with pytest.raises(ValueError, match="Missing calibrated look-at for A -> B"):
        prepare_dual_gaze_only_artifacts(
            manifest_path=_dual_manifest(tmp_path), character_mappings=mappings, cmds_module=cmds
        )


def _dual_manifest(tmp_path: Path) -> Path:
    artifacts = {}
    for alias, target in (("A", "B"), ("B", "A")):
        path = tmp_path / f"{alias}_events.json"
        path.write_text(json.dumps({"events": [{"channel": "gaze", "value": f"GAZE-{target}", "phrase_id": f"P{alias}", "resolved_time": {"start": 0.0, "end": 1.0}}]}), encoding="utf-8")
        artifacts[alias] = str(path)
    manifest = tmp_path / "dual_manifest.json"
    manifest.write_text(json.dumps({"schema_version": "dual_animation_manifest_v0", "fps": 24.0, "character_runtime_mapping": {"A": {"script_name": "AGNES", "sound_file": "SeqT_AGNES"}, "B": {"script_name": "WILL", "sound_file": "SeqT_WILL"}}, "artifacts": artifacts}), encoding="utf-8")
    return manifest


class _DualCmds:
    def __init__(self):
        self.calls = []; self.values = {
            "A:eyeStare_world.translateZ": 9.0, "B:eyeStare_world.translateZ": 11.0,
            "A:CNT_BOTH_EYES.translateX": 1.0, "A:CNT_BOTH_EYES.translateY": 2.0,
            "B:CNT_BOTH_EYES.translateX": 3.0, "B:CNT_BOTH_EYES.translateY": 4.0,
        }
    def objExists(self, _node): return True
    def getAttr(self, attribute): return self.values.get(attribute, "SeqT_AGNES" if "jSync1" in attribute else "SeqT_WILL")
    def cutKey(self, *args, **kwargs): self.calls.append(("cutKey", args, kwargs))
    def animLayer(self, *args, **kwargs): self.calls.append(("animLayer", args, kwargs)); return [] if kwargs.get("query") else args[0]
    def xform(self, *args, **kwargs): self.calls.append(("xform", args, kwargs))
    def setKeyframe(self, *args, **kwargs): self.calls.append(("setKeyframe", args, kwargs))
    def setAttr(self, *args, **kwargs): self.calls.append(("setAttr", args, kwargs))
    def keyframe(self, *_args, **kwargs):
        return [1.0] if kwargs.get("timeChange") else [0.25] if kwargs.get("valueChange") else []


def _dual_mappings():
    return {"A": {"script_name": "AGNES", "maya_node": "|A:ROOT", "gaze_targets": {"B": {"eye_stare_translate": [1, 2, 3], "eye_stare_world_position": [10, 0, 0]}}, "gaze_reference": {"eye_stare_world_position": [999, 999, 999]}}, "B": {"script_name": "WILL", "maya_node": "|B:ROOT", "gaze_targets": {"A": {"eye_stare_translate": [4, 5, 6], "eye_stare_world_position": [20, 0, 0]}}, "gaze_reference": {"eye_stare_world_position": [999, 999, 999]}}}


def _patch_dual_runtime(monkeypatch, cmds):
    import animation_apply_runner as runner
    import expregaze_jali.maya_apply_eye_performance as eye_module
    import expregaze_jali.maya_apply_gaze as gaze_module
    monkeypatch.setitem(sys.modules, "maya", SimpleNamespace(cmds=cmds))
    monkeypatch.setattr(gaze_module, "load_maya_gaze_config", lambda _path: {})
    monkeypatch.setattr(eye_module, "load_maya_eye_config", lambda _path: {})
    monkeypatch.setattr(eye_module, "apply_eye_performance_events", lambda **_kwargs: None)
    def resolve(node, expected, **_kwargs):
        return "|A:ROOT|jSync1" if node == "|A:ROOT" else "|B:ROOT|jSync2"
    monkeypatch.setattr(runner, "resolve_jsync_for_character", resolve)


def test_prepare_dual_overlay_is_non_destructive_and_resolves_both_jsync(monkeypatch, tmp_path):
    cmds = _DualCmds(); _patch_dual_runtime(monkeypatch, cmds)
    prepared = prepare_dual_animation_overlay(manifest_path=_dual_manifest(tmp_path), character_mappings=_dual_mappings())
    assert prepared["jsync_nodes"] == {"A": "|A:ROOT|jSync1", "B": "|B:ROOT|jSync2"}
    assert prepared["A"]["gaze_reference"]["eye_stare_translate"] == [0.0, 0.0, 9.0]
    assert prepared["A"]["gaze_reference"]["both_eyes_translate"] == [1.0, 2.0]
    assert prepared["A"]["eyelid_reference"] == {"node": "A:LIDS_jSync_plusMinus", "attr": "Down_upLids_jSync", "keys": [{"frame": 1.0, "value": 0.25}]}
    assert prepared["B"]["gaze_key_schedule"]
    assert cmds.calls == []


def test_dual_eye_adapter_preserves_event_provenance_and_snapshot_is_read_only():
    import animation_apply_runner as runner
    events = [{"id": "e6", "phrase_id": "P06", "source_proposal_id": "S06", "channel": "blink_suppression", "value": "SUPPRESS", "reason": "still", "resolved_time": {"start": 1, "end": 2}}]
    adapted = runner._dual_eye_events(events)
    assert adapted[0] == {"id": "e6", "phrase_id": "P06", "source_proposal_id": "S06", "reason": "still", "type": "blink_suppression", "value": "SUPPRESS", "mode": "SUPPRESS", "resolved_time": {"start": 1, "end": 2}}
    cmds = _DualCmds()
    assert capture_eyelid_animation_reference("A:lids", "lid", cmds_module=cmds)["keys"] == [{"frame": 1.0, "value": 0.25}]
    assert cmds.calls == []


def test_prepare_dual_overlay_fails_before_any_actor_is_modified(monkeypatch, tmp_path):
    cmds = _DualCmds(); _patch_dual_runtime(monkeypatch, cmds)
    mappings = _dual_mappings(); del mappings["B"]["gaze_targets"]["A"]
    with pytest.raises(ValueError, match="Character B requires an artist-captured gaze target"):
        prepare_dual_animation_overlay(manifest_path=_dual_manifest(tmp_path), character_mappings=mappings)
    assert cmds.calls == []


def test_prepare_dual_overlay_rejects_invalid_b_jsync_before_any_write(monkeypatch, tmp_path):
    cmds = _DualCmds(); _patch_dual_runtime(monkeypatch, cmds)
    import animation_apply_runner as runner
    def resolve(node, expected, **_kwargs):
        if node == "|B:ROOT":
            raise RuntimeError("No jSync node found beneath character '|B:ROOT'.")
        return "|A:ROOT|jSync1"
    monkeypatch.setattr(runner, "resolve_jsync_for_character", resolve)
    with pytest.raises(RuntimeError, match="No jSync node"):
        prepare_dual_animation_overlay(manifest_path=_dual_manifest(tmp_path), character_mappings=_dual_mappings())
    assert cmds.calls == []


def test_post_freeze_dual_apply_uses_prepared_context_without_jsync(monkeypatch, tmp_path):
    cmds = _DualCmds(); _patch_dual_runtime(monkeypatch, cmds)
    prepared = prepare_dual_animation_overlay(manifest_path=_dual_manifest(tmp_path), character_mappings=_dual_mappings())
    import animation_apply_runner as runner
    monkeypatch.setattr(runner, "resolve_jsync_for_character", lambda *_a, **_k: pytest.fail("apply must not resolve jSync"))
    result = apply_dual_animation_artifacts(prepared_context=prepared)
    assert result["jsync_nodes"]["A"].endswith("jSync1")
    assert {call[1][0] for call in cmds.calls if call[0] == "cutKey"} == {"A:eyeStare_world", "A:CNT_BOTH_EYES", "B:eyeStare_world", "B:CNT_BOTH_EYES"}
    assert any(call[0] == "setKeyframe" and call[1][0] == "A:eyeStare_world" for call in cmds.calls)
    assert any(call[0] == "setKeyframe" and call[1][0] == "B:eyeStare_world" for call in cmds.calls)


def test_post_freeze_dual_apply_uses_preserve_jali_blink_ownership(monkeypatch, tmp_path):
    cmds = _DualCmds(); _patch_dual_runtime(monkeypatch, cmds)
    manifest = _dual_manifest(tmp_path)
    for alias in ("A", "B"):
        path = tmp_path / f"{alias}_events.json"
        payload = json.loads(path.read_text())
        payload["events"].append({"id": f"{alias}blink", "phrase_id": f"P{alias}", "channel": "blink", "value": "DOUBLE_BLINK", "resolved_time": {"start": 0.0, "end": 1.0}})
        payload["events"].append({"id": f"{alias}suppress", "phrase_id": f"P{alias}", "channel": "blink_suppression", "value": "SUPPRESS", "resolved_time": {"start": 0.0, "end": 1.0}})
        payload["events"].append({"id": f"{alias}lid", "phrase_id": f"P{alias}", "channel": "lid", "value": -1, "resolved_time": {"start": 0.0, "end": 1.0}})
        path.write_text(json.dumps(payload))
    import expregaze_jali.maya_apply_eye_performance as eye_module
    calls = []
    monkeypatch.setattr(eye_module, "apply_eye_performance_events", lambda **kwargs: calls.append(kwargs))
    prepared = prepare_dual_animation_overlay(manifest_path=manifest, character_mappings=_dual_mappings())
    assert prepared["A"]["lid_event_count_compiled_deferred"] == 1
    assert prepared["A"]["eye_events"][0]["phrase_id"] == "PA"
    apply_dual_animation_artifacts(prepared_context=prepared)
    assert len(calls) == 2
    assert all(call["preserve_existing_regulatory_blinks"] is True and call["apply_lid_states"] is False and call["clear_existing_eyelid_keys"] is False for call in calls)


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


def test_dual_character_gaze_targets_require_explicit_nodes_not_jali_groups():
    mappings = {"A": {"maya_node": "|ValleyGirl:JALI_GRP", "look_at_node": "|ValleyGirl:look_LOC"}, "B": {"maya_node": "|Angela:JALI_GRP", "look_at_node": "|Angela:look_LOC"}}
    assert resolve_character_look_at_target("A", mappings) == "|ValleyGirl:look_LOC"
    assert resolve_character_look_at_target("B", mappings) == "|Angela:look_LOC"
    with pytest.raises(ValueError, match="JALI_GRP is not a gaze target"):
        resolve_character_look_at_target("A", {"A": {"maya_node": "|ValleyGirl:JALI_GRP"}})


def test_dual_gaze_adapter_sorts_and_preserves_identity_and_social_avert():
    events = [
        {"phrase_id": "P04", "source_proposal_id": "S04", "channel": "gaze", "value": "GAZE-B", "resolved_time": {"start": 136, "end": 140}},
        {"phrase_id": "P01", "source_proposal_id": "S01", "channel": "gaze", "value": "AVERT-B", "reason": "avoid", "resolved_time": {"start": 0, "end": 5}},
        {"phrase_id": "P03", "source_proposal_id": "S03", "channel": "gaze", "value": "GLANCE-B", "resolved_time": {"start": 100, "end": 110}},
        {"phrase_id": "P02", "source_proposal_id": "S02", "channel": "gaze", "value": "GAZE-A", "resolved_time": {"start": 86, "end": 90}},
    ]
    adapted = adapt_dual_gaze_events(events)
    assert [row["resolved_time"]["start"] for row in adapted] == [0, 86, 100, 136]
    assert adapted[0]["target"] == "__BASE__" and adapted[0]["social_avert"]
    assert adapted[1]["target"] == "A" and adapted[1]["phrase_id"] == "P02"
    assert adapted[0]["source_proposal_id"] == "S01" and adapted[0]["reason"] == "avoid"


def test_directional_eye_offsets_are_local_and_clamped():
    assert directional_eye_offset("DOWN_LEFT", magnitude=10, limit=6) == (-6.0, -6.0)
    assert directional_eye_offset("B", social=True) == (0.0, -5.0)


def test_gaze_state_machine_resets_detailed_eyes_after_avert():
    raw = [
        {"channel":"gaze","value":"AVERT-A","phrase_id":"P1","resolved_time":{"start":100,"end":136}},
        {"channel":"gaze","value":"GAZE-A","phrase_id":"P2","resolved_time":{"start":136,"end":200}},
    ]
    schedule = build_dual_gaze_schedule(adapt_dual_gaze_events(raw), neutral_position=[3,4,5], neutral_eyes=[1,2], target_positions={"A":[9,8,7]})
    assert schedule[0]["eye_stare"] == [3,4,5] and schedule[0]["eyes"] == [1, -3.0]
    assert schedule[0]["end"] == 136
    assert schedule[1]["eye_stare"] == [9,8,7] and schedule[1]["eyes"] == [1,2]


def test_first_gaze_at_timeline_start_initializes_directly_without_neutral_transition():
    raw = [{"channel": "gaze", "value": "GAZE-B", "resolved_time": {"start": 0, "end": 10}}]
    schedule = build_dual_gaze_schedule(adapt_dual_gaze_events(raw), neutral_position=[9, 9, 9], neutral_eyes=[4, 5], target_positions={"B": [1, 2, 3]})
    keys = build_dual_gaze_key_schedule(schedule, fps=1, transition_frames=3)
    assert keys == [{"frame": 0.0, "eye_stare": [1, 2, 3], "eyes": [4, 5]}]


def test_first_avert_at_timeline_start_initializes_its_complete_state():
    raw = [{"channel": "gaze", "value": "AVERT-DOWN", "resolved_time": {"start": 0, "end": 10}}]
    schedule = build_dual_gaze_schedule(adapt_dual_gaze_events(raw), neutral_position=[9, 8, 7], neutral_eyes=[1, 2], target_positions={})
    keys = build_dual_gaze_key_schedule(schedule, fps=1, transition_frames=3)
    assert keys == [{"frame": 0.0, "eye_stare": [9, 8, 7], "eyes": [1, -3.0]}]


def test_first_gaze_after_timeline_start_keeps_neutral_then_uses_transition():
    raw = [{"channel": "gaze", "value": "GAZE-B", "resolved_time": {"start": 2, "end": 10}}]
    schedule = build_dual_gaze_schedule(adapt_dual_gaze_events(raw), neutral_position=[9, 9, 9], neutral_eyes=[4, 5], target_positions={"B": [1, 2, 3]})
    keys = build_dual_gaze_key_schedule(schedule, fps=1, transition_frames=3)
    assert keys == [{"frame": 2.0, "eye_stare": [9, 9, 9], "eyes": [4, 5]}, {"frame": 5.0, "eye_stare": [1, 2, 3], "eyes": [4, 5]}]


def test_later_persistent_state_still_uses_explicit_transition_frames():
    raw = [{"channel": "gaze", "value": "GAZE-A", "resolved_time": {"start": 0, "end": 4}}, {"channel": "gaze", "value": "AVERT-DOWN", "resolved_time": {"start": 4, "end": 10}}]
    schedule = build_dual_gaze_schedule(adapt_dual_gaze_events(raw), neutral_position=[0, 0, 0], neutral_eyes=[0, 0], target_positions={"A": [1, 2, 3]})
    keys = build_dual_gaze_key_schedule(schedule, fps=1, transition_frames=3)
    assert {key["frame"] for key in keys if key["eye_stare"] == [1, 2, 3]} == {0.0, 4.0}
    assert any(key["frame"] == 7.0 and key["eye_stare"] == [0, 0, 0] and key["eyes"] == [0, -5.0] for key in keys)


def test_adapted_chain_and_glance_return_emit_complete_key_states():
    raw=[{"channel":"gaze","value":"GAZE-A","resolved_time":{"start":0,"end":20}},{"channel":"gaze","value":"GLANCE-B","resolved_time":{"start":5,"end":12}}]
    schedule=build_dual_gaze_schedule(adapt_dual_gaze_events(raw),neutral_position=[0,0,0],neutral_eyes=[0,0],target_positions={"A":[1,1,1],"B":[2,2,2]})
    keys=build_dual_gaze_key_schedule(schedule,fps=1,transition_frames=1,glance_min_hold_seconds=0.5)
    assert len(schedule)==2 and any(key["frame"]==12 and key["eye_stare"]==[1,1,1] and key["eyes"]==[0,0] for key in keys)


def test_glance_restores_persistent_previous_state_and_uses_own_end():
    raw=[{"channel":"gaze","value":"GAZE-A","resolved_time":{"start":0,"end":20}},{"channel":"gaze","value":"GLANCE-B","resolved_time":{"start":5,"end":6}},{"channel":"gaze","value":"GAZE-C","resolved_time":{"start":9,"end":20}}]
    schedule=build_dual_gaze_schedule(adapt_dual_gaze_events(raw),neutral_position=[0,0,0],neutral_eyes=[0,0],target_positions={"A":[1,0,0],"B":[2,0,0],"C":[3,0,0]})
    assert schedule[1]["end"]==6 and schedule[2]["previous_state"]["eye_stare"]==[1,0,0]


def test_glance_uses_rapid_out_hold_and_rapid_return_at_24_fps():
    raw = [
        {"channel": "gaze", "value": "GAZE-A", "resolved_time": {"start": 0, "end": 2}},
        {"channel": "gaze", "value": "GLANCE-B", "resolved_time": {"start": 2, "end": 3}},
    ]
    schedule = build_dual_gaze_schedule(adapt_dual_gaze_events(raw), neutral_position=[0, 0, 0], neutral_eyes=[4, 5], target_positions={"A": [1, 1, 1], "B": [2, 2, 2]})
    keys = build_dual_gaze_key_schedule(schedule, fps=24, transition_frames=3, glance_min_hold_seconds=0.5)
    glance_keys = [key for key in keys if key["frame"] >= 48]
    assert glance_keys == [
        {"frame": 48.0, "eye_stare": [1, 1, 1], "eyes": [4, 5]},
        {"frame": 51.0, "eye_stare": [2, 2, 2], "eyes": [4, 5]},
        {"frame": 69.0, "eye_stare": [2, 2, 2], "eyes": [4, 5]},
        {"frame": 72.0, "eye_stare": [1, 1, 1], "eyes": [4, 5]},
    ]
    assert not any(key["frame"] == 54.0 for key in glance_keys)


@pytest.mark.parametrize("transition_frames", [2, 3])
def test_glance_hold_stays_at_least_half_second_for_configured_transition(transition_frames):
    raw = [{"channel": "gaze", "value": "GLANCE-B", "resolved_time": {"start": 2, "end": 3}}]
    schedule = build_dual_gaze_schedule(adapt_dual_gaze_events(raw), neutral_position=[0, 0, 0], neutral_eyes=[4, 5], target_positions={"B": [2, 2, 2]})
    keys = build_dual_gaze_key_schedule(schedule, fps=24, transition_frames=transition_frames, glance_min_hold_seconds=0.5)
    assert keys[2]["frame"] - keys[1]["frame"] >= 12


def test_glance_too_short_for_transitions_and_half_second_hold_is_rejected():
    raw = [{"channel": "gaze", "value": "GLANCE-B", "resolved_time": {"start": 2, "end": 2.5}}]
    schedule = build_dual_gaze_schedule(adapt_dual_gaze_events(raw), neutral_position=[0, 0, 0], neutral_eyes=[4, 5], target_positions={"B": [2, 2, 2]})
    with pytest.raises(ValueError, match="GLANCE interval is too short"):
        build_dual_gaze_key_schedule(schedule, fps=24, transition_frames=3, glance_min_hold_seconds=0.5)


def test_clear_character_gaze_animation_is_attribute_scoped():
    calls=[]
    class Cmds:
        def cutKey(self,*args,**kwargs): calls.append((args,kwargs))
    clear_character_gaze_animation({"eye_stare_node":"eye","both_eyes_node":"both"},cmds_module=Cmds())
    assert calls == [(('eye',),{'attribute':'translateX','clear':True}),(('eye',),{'attribute':'translateY','clear':True}),(('eye',),{'attribute':'translateZ','clear':True}),(('both',),{'attribute':'translateX','clear':True}),(('both',),{'attribute':'translateY','clear':True})]


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
