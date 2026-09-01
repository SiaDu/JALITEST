from __future__ import annotations

from pathlib import Path
import json
import sys
from types import SimpleNamespace
import wave

import pytest


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from animation_apply_runner import (  # noqa: E402
    apply_animation_artifacts,
    apply_master_audio_to_maya_timeline,
    build_explicit_target_map,
    qualify_rig_control,
    adapt_dual_gaze_events,
    directional_eye_offset,
    build_dual_gaze_schedule,
    adapt_short_glance_schedule,
    build_dual_gaze_key_schedule,
    clear_character_gaze_animation,
    prepare_dual_animation_overlay,
    apply_dual_animation_artifacts,
    apply_dual_speaker_emotion_artifacts,
    apply_dual_listener_mask_artifacts,
    build_listener_mask_key_schedule,
    build_v2_listener_mask_key_schedule,
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
    build_blink_brow_companion_key_schedule,
    build_fixation_micro_saccade_key_schedule,
    fixation_gaze_intervals,
    plan_fixation_micro_saccades,
    apply_dual_v2_head_blink_overlays,
    prepare_dual_v2_head_blink_overlays,
    prepare_dual_v2_gaze_only_artifacts,
    plan_v2_blinks,
    inject_idle_regulatory_blinks,
    diagnose_v2_blink_ownership,
    prepare_dual_v2_listener_mask_artifacts,
    _v2_overlay_config,
    _resolve_user_blink_brow_plugs,
    micro_saccade_layer_name,
    master_audio_timeline_info,
    idle_head_layer_name,
    plan_idle_head_drift,
)
from listener_mask_library import AU_TO_USER_CONTROL, FACTORY_MASK_AUS, user_pose_for_mask  # noqa: E402
from diagnose_eyelid_user_mappings import diagnose_eyelid_user_mappings  # noqa: E402


class _AlignmentMel:
    def __init__(self, log=None):
        self.calls = []
        self.log = log
        self.globals = {
            "silence_handling": 1,
            "silence_handling_decibel": -35.0,
            "jali_afscratch": 0,
        }
        self.getters = {}
        self.settings_at_realign = []

    def eval(self, command):
        import re

        self.calls.append(command)
        if self.log is not None:
            self.log.append(("mel", command))
        exists = re.fullmatch(r'exists "([^"]+)"', command)
        if exists:
            return int(
                exists.group(1) == "realign_node"
                or exists.group(1) in self.getters
            )
        definition = re.search(
            r"global proc (?:int|float) (\w+)\(\).*\$(\w+); return",
            command,
        )
        if definition:
            self.getters[definition.group(1)] = definition.group(2)
            return None
        getter = re.fullmatch(r"(jalitest_get_\w+)\(\)", command)
        if getter:
            return self.globals[self.getters[getter.group(1)]]
        setter = re.fullmatch(
            r"global (int|float) \$(\w+); \$\2 = (-?\d+(?:\.\d+)?);",
            command,
        )
        if setter:
            self.globals[setter.group(2)] = (
                int(float(setter.group(3)))
                if setter.group(1) == "int"
                else float(setter.group(3))
            )
            return None
        if command.startswith('realign_node "'):
            self.settings_at_realign.append(dict(self.globals))
        return None


def _write_wav(path: Path, *, seconds: float, sample_rate: int = 100) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\0\0" * int(seconds * sample_rate))


class _TimelineCmds:
    def __init__(self, sounds=None, *, animation_start=-12.0, maximum=240.0):
        self.sounds = {
            node: {"file": str(row["file"]), "offset": float(row.get("offset", 0))}
            for node, row in (sounds or {}).items()
        }
        self.animation_start = float(animation_start)
        self.options = {
            "minTime": 0.0,
            "maxTime": float(maximum),
            "animationEndTime": float(maximum),
        }
        self.time_controls = []
        self.sound_edits = []

    def ls(self, **kwargs):
        assert kwargs == {"type": "audio"}
        return list(self.sounds)

    def sound(self, node=None, **kwargs):
        if kwargs.get("query") and kwargs.get("file"):
            return self.sounds[str(node)]["file"]
        if kwargs.get("edit"):
            self.sounds[str(node)]["offset"] = float(kwargs["offset"])
            self.sound_edits.append(str(node))
            return node
        base = str(kwargs["name"])
        created = base
        suffix = 1
        while created in self.sounds:
            created = f"{base}{suffix}"; suffix += 1
        self.sounds[created] = {
            "file": str(kwargs["file"]),
            "offset": float(kwargs["offset"]),
        }
        return created

    def getAttr(self, plug):
        node, attribute = plug.rsplit(".", 1)
        if attribute == "filename":
            return self.sounds[node]["file"]
        raise KeyError(plug)

    def timeControl(self, slider, **kwargs):
        self.time_controls.append((slider, dict(kwargs)))

    def playbackOptions(self, **kwargs):
        if kwargs.get("query") and kwargs.get("animationStartTime"):
            return self.animation_start
        if "animationStartTime" in kwargs:
            self.animation_start = float(kwargs["animationStartTime"])
        self.options.update({key: float(value) for key, value in kwargs.items()})


def test_master_audio_duration_uses_ceil_and_rejects_zero_length(tmp_path):
    wav = tmp_path / "SeqT.wav"; _write_wav(wav, seconds=42)
    assert master_audio_timeline_info(wav, 30) == {
        "path": str(wav.resolve()),
        "seconds": 42.0,
        "fps": 30.0,
        "end_frame": 1260,
    }
    fractional = tmp_path / "fractional.wav"; _write_wav(fractional, seconds=1.01)
    assert master_audio_timeline_info(fractional, 30)["end_frame"] == 31
    empty = tmp_path / "empty.wav"; _write_wav(empty, seconds=0)
    with pytest.raises(ValueError, match="positive sample rate and audio length"):
        master_audio_timeline_info(empty, 30)


def test_master_audio_timeline_reuses_node_preserves_other_audio_and_negative_start(tmp_path):
    master = tmp_path / "SeqT.wav"; _write_wav(master, seconds=42)
    agnes = tmp_path / "SeqT_AGNES.wav"; _write_wav(agnes, seconds=20)
    will = tmp_path / "SeqT_WILL.wav"; _write_wav(will, seconds=18)
    other = tmp_path / "reference.wav"; _write_wav(other, seconds=3)
    sounds = {
        "agnesAudio": {"file": agnes, "offset": 4},
        "willAudio": {"file": will, "offset": 7},
        "referenceAudio": {"file": other, "offset": 9},
        "existingMaster": {"file": master.resolve(), "offset": 22},
    }
    cmds = _TimelineCmds(sounds, animation_start=-24, maximum=2000)
    mel = SimpleNamespace(eval=lambda expression: "timeControl1" if "$gPlayBackSlider" in expression else "")

    result = apply_master_audio_to_maya_timeline(
        master, 30, cmds_module=cmds, mel_module=mel
    )
    again = apply_master_audio_to_maya_timeline(
        master, 30, cmds_module=cmds, mel_module=mel
    )

    assert result["audio_node"] == again["audio_node"] == "existingMaster"
    assert result["audio_node_reused"] is True and len(cmds.sounds) == 4
    assert cmds.sounds["existingMaster"]["offset"] == 0
    assert cmds.sounds["agnesAudio"]["offset"] == 4
    assert cmds.sounds["willAudio"]["offset"] == 7
    assert cmds.sounds["referenceAudio"]["offset"] == 9
    assert cmds.animation_start == -24
    assert cmds.options == {"minTime": 0.0, "maxTime": 1260.0, "animationEndTime": 1260.0}
    assert cmds.time_controls[-1] == (
        "timeControl1",
        {"edit": True, "sound": "existingMaster", "displaySound": True},
    )


def test_master_audio_timeline_creates_at_zero_and_can_lengthen_and_shorten(tmp_path):
    long_wav = tmp_path / "long.wav"; _write_wav(long_wav, seconds=10)
    short_wav = tmp_path / "short.wav"; _write_wav(short_wav, seconds=2)
    cmds = _TimelineCmds(animation_start=10, maximum=100)
    mel = SimpleNamespace(eval=lambda _expression: "playbackSlider")

    long_result = apply_master_audio_to_maya_timeline(
        long_wav, 30, cmds_module=cmds, mel_module=mel
    )
    assert long_result["audio_node_reused"] is False
    assert cmds.sounds["long"]["offset"] == 0
    assert cmds.options["maxTime"] == 300
    assert cmds.animation_start == 0

    short_result = apply_master_audio_to_maya_timeline(
        short_wav, 30, cmds_module=cmds, mel_module=mel
    )
    assert short_result["audio_node"] == "short"
    assert cmds.options["minTime"] == 0
    assert cmds.options["maxTime"] == cmds.options["animationEndTime"] == 60
    assert len(cmds.sounds) == 2


def test_v2_overlay_config_uses_maya_safe_yaml_fallback_without_pyyaml(monkeypatch):
    import builtins

    original_import = builtins.__import__
    def no_pyyaml(name, *args, **kwargs):
        if name == "yaml":
            raise ModuleNotFoundError("No module named 'yaml'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyyaml)
    config = _v2_overlay_config()
    assert config["head"]["control_suffix"] == "jNeck_ctl"
    assert (config["head"]["attack_frames"], config["head"]["settle_frames"], config["head"]["overshoot_ratio"]) == (15, 10, 0.20)
    assert config["affect"]["transition_frames"] == 12
    assert config["blink"]["presets"]["BLINK"]["closure"] == 7
    assert config["blink"]["brow_companion"]["delta"] == 2
    assert config["blink"]["brow_companion"]["central_control_suffix"] == "usr_BrowInDown"
    assert config["blink"]["brow_companion"]["central_attribute"] == "BrowDown"
    assert (config["blink"]["brow_companion"]["left_attribute"], config["blink"]["brow_companion"]["right_attribute"]) == ("BrowDown_L", "BrowDown_R")
    assert config["blink"]["idle_regulatory"] == {"enabled": True, "min_interval_seconds": 3.5, "max_interval_seconds": 6.0, "min_separation_seconds": 0.75}
    micro = config["micro_saccade"]
    assert {key: micro[key] for key in ("enabled", "move_frames", "hold_frames")} == {"enabled": True, "move_frames": 2, "hold_frames": 10}
    assert micro["points"] == {"A": [-.28, .10], "B": [0.0, -.18], "C": [.24, .08]}
    assert config["idle_head"]["max_pitch_degrees"] == .80 and config["idle_head"]["max_roll_degrees"] == .45


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
        def getAttr(self, plug):
            if plug.endswith(".sound_file"): return "SA" if "jSync1" in plug else "SB"
            if plug.endswith(".silence_handling"): return True
            if plug.endswith(".silence_handling_decibel"): return -60.0
            return "original/"
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
        def getAttr(self,p):
            if p.endswith(".silence_handling"): return True
            if p.endswith(".silence_handling_decibel"): return -60.0
            return self.values.get(p, "SA" if "jSync1.sound_file" in p else "SB" if "jSync2.sound_file" in p else "original/")
        def attributeQuery(self,*_a,**_k): return ["from Annotation:From Transcript Tags"]
        def setAttr(self,*a,**k): self.values[a[0]]=a[1]; self.calls.append(a)
        def ls(self,**k): return self.selection if k.get("selection") else []
        def select(self,items=None,**k): self.selection=[] if k.get("clear") else list(items or [])
    cmds=Cmds(); mel=_AlignmentMel()
    monkeypatch.setattr(runner,"resolve_jsync_for_character",lambda rig,*_a,**_k: "|A:ROOT|jSync1" if rig == "|A:ROOT" else "|B:ROOT|jSync2")
    result=apply_dual_speaker_emotion_artifacts(manifest_path=manifest,character_mappings={"A":{"maya_node":"|A:ROOT"},"B":{"maya_node":"|B:ROOT"}},cmds_module=cmds,mel_module=mel)
    assert all(Path(result[a]["staging_txt"]).read_text().startswith("<mask=") for a in ("A","B"))
    assert Path(result["A"]["staging_wav"]).read_bytes() == b"A" and Path(result["B"]["staging_wav"]).read_bytes() == b"B"
    assert all(result[a]["paths_restored"] for a in ("A","B")) and sum(call.startswith('realign_node ') for call in mel.calls) == 2
    assert mel.settings_at_realign == [
        {"silence_handling": 1, "silence_handling_decibel": -60.0, "jali_afscratch": 1},
        {"silence_handling": 1, "silence_handling_decibel": -60.0, "jali_afscratch": 1},
    ]
    assert mel.globals == {
        "silence_handling": 1,
        "silence_handling_decibel": -35.0,
        "jali_afscratch": 0,
    }
    assert all(result[a]["jali_settings"] == {"filter_silence_gaps": True, "silence_threshold_db": -60.0} for a in ("A", "B"))


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
            if plug.endswith(".silence_handling"): return True
            if plug.endswith(".silence_handling_decibel"): return -60.0
            return self.values.get(plug, "original/")
        def setAttr(self, plug, value, **_kwargs): self.values[plug] = value; log.append(("set", plug, value))
        def ls(self, **_kwargs): return []
        def select(self, *_args, **_kwargs): pass
    cmds = Cmds()
    monkeypatch.setattr(runner, "resolve_jsync_for_character", lambda rig, *_a, **_k: rig + "|" + ("ALICE_jSync" if "ALICE" in rig else "BOB_jSync"))
    result = apply_dual_speaker_emotion_artifacts(manifest_path=manifest, character_mappings={"ALICE": {"maya_node": "|ALICE:ROOT"}, "BOB": {"maya_node": "|BOB:ROOT"}}, cmds_module=cmds, mel_module=_AlignmentMel(log=log))
    for actor in ("ALICE", "BOB"):
        plug = f"|{actor}:ROOT|{actor}_jSync.calculate_blinks"
        disable_index = log.index(("set", plug, False))
        realign_index = next(index for index, row in enumerate(log) if row[0] == "mel" and "realign_node" in row[1] and actor in row[1])
        assert disable_index < realign_index
        assert result[actor]["calculate_blinks"] is False


def test_confused_25_scales_factory_coefficients_including_expressive_eyelids():
    pose = user_pose_for_mask("Confused-25")
    assert pose["usr_InnerBrowRaise_L.InnerBrowRaise_L"] == 1.25
    assert pose["usr_OuterBrowRaise_R.OuterBrowRaise_R"] == 1.5
    assert pose["usr_BrowInDown_L.BrowIn_L"] == 1.875
    assert pose["usr_Wince_R.Wince_R"] == 0.5
    assert pose["usr_Pucker_L.Pucker_L"] == 0.5
    assert pose["usr_Squint_R.Squint_R"] == 0.5
    assert "au05_uLidUpL" in FACTORY_MASK_AUS["Confused"]
    assert pose["usr_blink_L.LidDown_L"] == -0.75
    assert pose["usr_blink_R.LidDown_R"] == -0.75


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
    assert result["B"]["expressive_eyelids_mapped"] is True and result["A"]["FACS_animationSource"] == "Add"
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


def test_v2_initial_affect_is_active_for_both_actors_from_scene_start(tmp_path):
    artifacts = {"characters": {}}
    for actor, state in (
        ("ALICE", {"affect": "Watchful-80", "gaze": "GAZE-BOB", "head": "HEAD-NONE"}),
        ("BOB", {"affect": "Watchful-85", "gaze": "GAZE-ALICE", "head": "HEAD-NONE"}),
    ):
        path = tmp_path / f"{actor}.json"
        path.write_text(json.dumps({"initial_state": {"actor": actor, "timing_role": "INITIAL_STATE", "resolved_start": 0.0, "state": state}, "events": []}))
        artifacts["characters"][actor] = {"resolved_sparse_events": str(path)}
    timing = tmp_path / "anchor_timing.json"
    timing.write_text(json.dumps({
        "w0001": {"speaker": "ALICE", "turn_id": "T01", "text": "Hello", "start": .5, "end": .7},
        "w0002": {"speaker": "BOB", "turn_id": "T02", "text": "No.", "start": 2.0, "end": 2.2},
        "w0003": {"speaker": "ALICE", "turn_id": "T03", "text": "Again", "start": 3.0, "end": 3.2},
    }))
    artifacts["conversation_anchor_timing"] = str(timing)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "dual_animation_manifest_v2", "characters": ["ALICE", "BOB"], "fps": 24,
        "shared_duration_seconds": 4.0,
        "character_runtime_mapping": {"ALICE": {"script_name": "ALICE", "sound_file": "A"}, "BOB": {"script_name": "BOB", "sound_file": "B"}},
        "artifacts": artifacts,
    }))
    prepared = prepare_dual_v2_listener_mask_artifacts(
        manifest_path=manifest,
        character_mappings={"ALICE": {"maya_node": "|ALICE:ROOT"}, "BOB": {"maya_node": "|BOB:ROOT"}},
        cmds_module=_ListenerCmds(),
    )
    assert prepared["expressive_eyelid_mapping_requirement"] == []
    assert {"ALICE:usr_blink_L.LidDown_L", "ALICE:usr_blink_R.LidDown_R", "ALICE:usr_loLid_L.LidUp_L", "ALICE:usr_loLid_R.LidUp_R"} <= set(prepared["ALICE"]["managed_user_plugs"])
    assert prepared["ALICE"]["timeline"][0]["start"] == 0.0
    assert prepared["ALICE"]["timeline"][0]["state"] == "Watchful-80"
    assert [(row["start"], row["state"]) for row in prepared["ALICE"]["timeline"]] == [(0.0, "Watchful-80")]
    assert prepared["BOB"]["timeline"][0]["start"] == 0.0
    assert prepared["BOB"]["timeline"][0]["state"] == "Watchful-85"
    assert [(row["start"], row["state"]) for row in prepared["BOB"]["timeline"]] == [(0.0, "Watchful-85")]


def test_v2_glance_returns_to_persistent_gaze_before_clip_end():
    events = [
        {"mode": "GAZE", "target": "BOB", "resolved_time": {"start": 0.0, "end": 2.0}},
        {"mode": "GLANCE", "target": "DOWN", "resolved_time": {"start": 2.0, "end": 2.75}},
    ]
    schedule = build_dual_gaze_schedule(events, neutral_position=[0, 0, 0], neutral_eyes=[0, 0], target_positions={"BOB": [1, 2, 3]})
    keys = build_dual_gaze_key_schedule(schedule, fps=24, transition_frames=3, glance_transition_frames=3, glance_hold_seconds=.5, allow_shortened_glance=True)
    assert schedule[1]["return_state"]["eye_stare"] == [1, 2, 3]
    assert any(key["frame"] == 66.0 and key["eye_stare"] == [1, 2, 3] for key in keys)


def test_v2_glance_returns_to_persistent_gaze_until_later_authored_gaze():
    events = [
        {"mode": "GAZE", "target": "BOB", "resolved_time": {"start": 0.0, "end": 2.0}},
        {"mode": "GLANCE", "target": "DOWN", "resolved_time": {"start": 2.0, "end": 2.75}},
        {"mode": "GAZE", "target": "RIGHT", "resolved_time": {"start": 4.0, "end": 6.0}},
    ]
    schedule = build_dual_gaze_schedule(events, neutral_position=[0, 0, 0], neutral_eyes=[0, 0], target_positions={"BOB": [1, 2, 3]})
    keys = build_dual_gaze_key_schedule(schedule, fps=24, transition_frames=3, glance_transition_frames=3, glance_hold_seconds=.5, allow_shortened_glance=True)
    assert schedule[1]["end"] == 2.75
    assert any(key["frame"] == 66.0 and key["eye_stare"] == [1, 2, 3] for key in keys)
    assert any(key["frame"] == 99.0 and key["eyes"] == [5.0, 0.0] for key in keys)


def test_v2_head_schedule_is_additive_config_driven_and_none_returns_zero():
    config = {"attack_frames": 15, "settle_frames": 10, "overshoot_ratio": .2, "strength_degrees": {"SUBTLE": 3, "MEDIUM": 6, "STRONG": 10}, "pitch_axis": "rotateX", "roll_axis": "rotateZ", "pitch_up_sign": -1, "tilt_left_sign": 1}
    events = [
        {"event_id": "E1", "resolved_start": 1.0, "changes": {"head": "HEAD-UP-STRONG"}},
        {"event_id": "E2", "resolved_start": 2.0, "changes": {"head": "HEAD-NONE"}},
    ]
    keys = build_head_overlay_key_schedule(events, fps=24, config=config)
    assert keys[1] == {"frame": 39.0, "values": {"rotateX": -12.0, "rotateY": 0.0, "rotateZ": 0.0}, "event_id": "E1"}
    assert keys[2]["values"]["rotateX"] == -10.0
    assert keys[-2]["values"]["rotateX"] == 2.0
    assert keys[-1]["values"] == {"rotateX": 0.0, "rotateY": 0.0, "rotateZ": 0.0}


def test_v2_head_realization_preserves_semantic_timing_roles():
    config = {"attack_frames": 15, "settle_frames": 10, "overshoot_ratio": .2, "strength_degrees": {"SUBTLE": 3, "MEDIUM": 6, "STRONG": 10}, "pitch_axis": "rotateX", "roll_axis": "rotateZ", "pitch_up_sign": -1, "tilt_left_sign": 1}
    initial = build_head_overlay_key_schedule([{"event_id": "I", "timing_role": "INITIAL_STATE", "resolved_start": 0, "changes": {"head": "HEAD-UP-SUBTLE"}}], fps=24, config=config)
    listener = build_head_overlay_key_schedule([{"event_id": "L", "timing_role": "LISTEN_REACTION", "resolved_start": 2, "changes": {"head": "HEAD-UP-SUBTLE"}}], fps=24, config=config)
    speaker = build_head_overlay_key_schedule([{"event_id": "S", "timing_role": "SPEAK_ONSET", "resolved_start": 2, "changes": {"head": "HEAD-UP-SUBTLE"}}], fps=24, config=config)
    assert initial == [{"frame": 0.0, "values": {"rotateX": -3.0, "rotateY": 0.0, "rotateZ": 0.0}, "event_id": "I"}]
    assert [key["frame"] for key in listener] == [48.0, 63.0, 73.0]
    assert [key["frame"] for key in speaker] == [33.0, 48.0, 58.0]
    assert not any(key["frame"] < 48.0 for key in listener)


def test_v2_head_overshoot_uses_movement_delta_and_clamps_speaker_attack():
    config = {"attack_frames": 15, "settle_frames": 10, "overshoot_ratio": .2, "strength_degrees": {"SUBTLE": 3, "MEDIUM": 6, "STRONG": 10}, "pitch_axis": "rotateX", "roll_axis": "rotateZ", "pitch_up_sign": -1, "tilt_left_sign": 1}
    events = [
        {"event_id": "A", "timing_role": "INITIAL_STATE", "resolved_start": 0, "changes": {"head": "HEAD-DOWN-MEDIUM"}},
        {"event_id": "B", "timing_role": "SPEAK_ONSET", "resolved_start": 2, "changes": {"head": "HEAD-DOWN-STRONG"}},
        {"event_id": "C", "timing_role": "SPEAK_ONSET", "resolved_start": 3, "changes": {"head": "HEAD-NONE"}},
    ]
    keys = build_head_overlay_key_schedule(events, fps=30, config=config)
    assert keys[0] == {"frame": 0.0, "values": {"rotateX": 6.0, "rotateY": 0.0, "rotateZ": 0.0}, "event_id": "A"}
    assert any(key["frame"] == 60.0 and key["values"]["rotateX"] == 10.8 for key in keys)
    assert any(key["frame"] == 90.0 and key["values"]["rotateX"] == -2.0 for key in keys)
    early = build_head_overlay_key_schedule([{"event_id": "E", "timing_role": "SPEAK_ONSET", "resolved_start": 8 / 30, "changes": {"head": "HEAD-DOWN-SUBTLE"}}], fps=30, config=config)
    assert [key["frame"] for key in early] == [0.0, 8.0, 18.0]


def test_v2_user_mask_schedule_interpolates_without_shifting_semantic_boundaries():
    watchful = {"value": 1.0}; nervous = {"value": 2.0}
    listener = build_v2_listener_mask_key_schedule([
        {"phrase_id": "INITIAL_STATE", "start": 0, "pose": watchful, "boundary_kind": "INITIAL_STATE"},
        {"phrase_id": "listen", "start": 2, "pose": nervous, "boundary_kind": "affect", "timing_role": "LISTEN_REACTION"},
    ], fps=30)
    speaker_affect = build_v2_listener_mask_key_schedule([
        {"phrase_id": "INITIAL_STATE", "start": 0, "pose": watchful, "boundary_kind": "INITIAL_STATE"},
        {"phrase_id": "speak", "start": 2, "pose": nervous, "boundary_kind": "affect", "timing_role": "SPEAK_ONSET"},
    ], fps=30)
    assert [(key["frame"], key["pose"]) for key in listener] == [(0.0, watchful), (60.0, watchful), (72.0, nervous)]
    assert [(key["frame"], key["pose"]) for key in speaker_affect] == [(0.0, watchful), (48.0, watchful), (60.0, nervous)]


def test_v2_listener_mask_ownership_is_per_actor_for_overlapping_turns(tmp_path):
    artifacts = {"characters": {}}
    for actor, affect in (("ALICE", "Watchful-80"), ("BOB", "Nervous-60")):
        path = tmp_path / f"{actor}.json"
        path.write_text(json.dumps({"initial_state": {"state": {"affect": affect}}, "events": []}))
        artifacts["characters"][actor] = {"resolved_sparse_events": str(path)}
    timing = tmp_path / "timing.json"
    timing.write_text(json.dumps({
        "a": {"speaker": "ALICE", "turn_id": "T01", "start": 1.0, "end": 3.0},
        "b": {"speaker": "BOB", "turn_id": "T02", "start": 2.8, "end": 4.0},
    }))
    artifacts["conversation_anchor_timing"] = str(timing)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "dual_animation_manifest_v2", "characters": ["ALICE", "BOB"], "fps": 24, "shared_duration_seconds": 5, "character_runtime_mapping": {"ALICE": {"script_name": "ALICE", "sound_file": "A"}, "BOB": {"script_name": "BOB", "sound_file": "B"}}, "artifacts": artifacts}))
    prepared = prepare_dual_v2_listener_mask_artifacts(manifest_path=manifest, character_mappings={"ALICE": {"maya_node": "|ALICE:ROOT"}, "BOB": {"maya_node": "|BOB:ROOT"}}, cmds_module=_ListenerCmds())
    alice = [(row["start"], row["state"]) for row in prepared["ALICE"]["timeline"]]
    bob = [(row["start"], row["state"]) for row in prepared["BOB"]["timeline"]]
    assert alice == [(0.0, "Watchful-80")]
    assert bob == [(0.0, "Nervous-60")]
    prepared_cmds = _ListenerCmds()
    result = apply_dual_listener_mask_artifacts(prepared_context=prepared, cmds_module=prepared_cmds)
    assert result["ALICE"]["FACS_animationSource"] == "Add"
    assert ("setAttr", ("ALICE:FACSMaster.FACS_animationSource", 2), {}) in prepared_cmds.calls


def test_v2_user_mask_affect_persists_through_turn_boundaries_until_next_affect(tmp_path):
    artifacts = {"characters": {}}
    chayton_events = tmp_path / "CHAYTON.json"
    chayton_events.write_text(json.dumps({
        "initial_state": {"state": {"affect": "Nervous-78"}},
        "events": [{"event_id": "E_AFFECT", "resolved_start": 6.0, "timing_role": "LISTEN_REACTION", "changes": {"affect": "Dislike-70"}}],
    }))
    joan_events = tmp_path / "JOAN.json"
    joan_events.write_text(json.dumps({"initial_state": {"state": {"affect": "MASK-NONE"}}, "events": []}))
    artifacts["characters"] = {
        "CHAYTON": {"resolved_sparse_events": str(chayton_events)},
        "JOAN": {"resolved_sparse_events": str(joan_events)},
    }
    timing = tmp_path / "timing.json"
    timing.write_text(json.dumps({
        "w1": {"speaker": "CHAYTON", "turn_id": "T01", "start": 1.0, "end": 1.5},
        "w2": {"speaker": "JOAN", "turn_id": "T02", "start": 2.0, "end": 3.0},
        "w3": {"speaker": "CHAYTON", "turn_id": "T03", "start": 4.0, "end": 4.5},
    }))
    artifacts["conversation_anchor_timing"] = str(timing)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "dual_animation_manifest_v2", "characters": ["CHAYTON", "JOAN"], "fps": 24,
        "shared_duration_seconds": 8.0,
        "character_runtime_mapping": {"CHAYTON": {"script_name": "CHAYTON", "sound_file": "C"}, "JOAN": {"script_name": "JOAN", "sound_file": "J"}},
        "artifacts": artifacts,
    }))
    prepared = prepare_dual_v2_listener_mask_artifacts(
        manifest_path=manifest,
        character_mappings={"CHAYTON": {"maya_node": "|CHAYTON:ROOT"}, "JOAN": {"maya_node": "|JOAN:ROOT"}},
        cmds_module=_ListenerCmds(),
    )
    timeline = [(row["start"], row["state"]) for row in prepared["CHAYTON"]["timeline"]]
    assert timeline == [(0.0, "Nervous-78"), (6.0, "Dislike-70")]
    keys = prepared["CHAYTON"]["key_schedule"]
    assert [key["frame"] for key in keys] == [0.0, 144.0, 156.0]
    assert keys[0]["pose"] == user_pose_for_mask("Nervous-78")
    assert keys[-1]["pose"] == user_pose_for_mask("Dislike-70")


def test_eyelid_mapping_probe_is_read_only_and_reports_exact_plug_edges():
    class Cmds:
        def objExists(self, plug): return plug == "Angela:L_LidJoint_Up.rotateX"
        def listConnections(self, plug, **_kwargs):
            return ["Angela:usr_Squint_L.OuterSquint_L", plug] if plug == "Angela:L_LidJoint_Up.rotateX" else []
    report = diagnose_eyelid_user_mappings("|Angela:ROOT", cmds_module=Cmds())
    assert report["Angela:L_LidJoint_Up.rotateX"] == [("Angela:usr_Squint_L.OuterSquint_L", "Angela:L_LidJoint_Up.rotateX")]
    assert report["Angela:R_LidJoint_Up.rotateX"] == []


def test_v2_blink_schedule_contains_only_explicit_performative_events():
    config = {"open_value": 0, "presets": {"DOUBLE_BLINK": {"closure": 7, "close_frames": 2, "hold_frames": 1, "open_frames": 2, "count": 2, "gap_frames": 4}}}
    keys = build_blink_overlay_key_schedule([{"event_id": "E1", "resolved_start": 1.0, "changes": {"blink": "DOUBLE_BLINK"}}], fps=24, config=config)
    assert len(keys) == 8 and keys[0]["frame"] == 24 and keys[1]["value"] == 7 and keys[-1]["value"] == 0


def test_v2_blink_brow_companion_uses_additive_delta_and_visual_start_frame():
    config = _v2_overlay_config()["blink"]
    normal = [{"event_id": "B", "resolved_start": 1, "blink_source": "gaze_regulatory", "changes": {"blink": "BLINK"}}]
    visual = [{"event_id": "V", "resolved_start": 1, "blink_source": "affect_regulatory", "visual_start_frame": 50, "changes": {"blink": "BLINK"}}]
    assert [(key["frame"], key["value"]) for key in build_blink_brow_companion_key_schedule(normal, fps=24, config=config)] == [(24.0, 0.0), (26.0, 2.0), (27.0, 2.0), (29.0, 0.0)]
    assert [key["frame"] for key in build_blink_brow_companion_key_schedule(visual, fps=24, config=config)] == [50.0, 52.0, 53.0, 55.0]


def test_v2_blink_brow_companion_covers_slow_double_and_hold_release():
    config = _v2_overlay_config()["blink"]
    slow = build_blink_brow_companion_key_schedule([{"event_id": "S", "resolved_start": 1, "changes": {"blink": "SLOW_BLINK"}}], fps=24, config=config)
    assert [(key["frame"], key["value"]) for key in slow] == [(24.0, 0.0), (29.0, 2.0), (33.0, 2.0), (39.0, 0.0)]
    double = build_blink_brow_companion_key_schedule([{"event_id": "D", "resolved_start": 1, "changes": {"blink": "DOUBLE_BLINK"}}], fps=24, config=config)
    assert [(key["frame"], key["value"]) for key in double] == [(24.0, 0.0), (26.0, 2.0), (27.0, 2.0), (29.0, 0.0), (33.0, 0.0), (35.0, 2.0), (36.0, 2.0), (38.0, 0.0)]
    hold = build_blink_brow_companion_key_schedule([
        {"event_id": "H", "resolved_start": 1, "changes": {"blink": "EYE_CLOSE_HOLD"}},
        {"event_id": "O", "resolved_start": 3, "changes": {"blink": "EYE_OPEN"}},
    ], fps=24, config=config)
    assert [(key["frame"], key["value"]) for key in hold] == [(24.0, 0.0), (28.0, 2.0), (72.0, 2.0), (76.0, 0.0)]


def test_v2_blink_brow_companion_is_independent_of_semantic_affect_value():
    config = _v2_overlay_config()["blink"]
    keys = build_blink_brow_companion_key_schedule([{"event_id": "B", "resolved_start": 1, "changes": {"blink": "BLINK"}}], fps=24, config=config)
    assert [key["value"] for key in keys] == [0.0, 2.0, 2.0, 0.0]


def test_v2_blink_brow_resolver_prefers_central_then_requires_both_sides():
    config = _v2_overlay_config()["blink"]
    class Cmds:
        def __init__(self, existing): self.existing = set(existing)
        def objExists(self, plug): return plug in self.existing
    central = "ALICE:usr_BrowInDown.BrowDown"
    left = "ALICE:usr_BrowInDown_L.BrowDown_L"
    right = "ALICE:usr_BrowInDown_R.BrowDown_R"
    assert _resolve_user_blink_brow_plugs("|ALICE:ROOT", config, Cmds([central])) == [central]
    assert _resolve_user_blink_brow_plugs("|ALICE:ROOT", config, Cmds([left, right])) == [left, right]
    with pytest.raises(RuntimeError, match="BrowDown control"):
        _resolve_user_blink_brow_plugs("|ALICE:ROOT", config, Cmds([left]))


def test_v2_eye_close_hold_persists_until_explicit_eye_open_and_suppresses_regulatory_blinks():
    config = {"open_value": 0, "presets": {"EYE_CLOSE_HOLD": {"closure": 9, "close_frames": 4}}}
    events = [
        {"event_id": "HOLD", "resolved_start": 1, "changes": {"blink": "EYE_CLOSE_HOLD"}},
        {"event_id": "OPEN", "resolved_start": 3, "changes": {"blink": "EYE_OPEN"}},
    ]
    assert [(key["frame"], key["value"]) for key in build_blink_overlay_key_schedule(events, fps=24, config=config)] == [(24.0, 0.0), (28.0, 9.0), (72.0, 9.0), (76.0, 0.0)]
    planned = plan_v2_blinks([
        _resolved("HOLD", 1, blink="EYE_CLOSE_HOLD"),
        _resolved("AFFECT", 2, affect="Nervous-80"),
        _resolved("OPEN", 3, blink="EYE_OPEN"),
    ], initial_state={"affect": "Neutral-60", "gaze": "GAZE-BOB"})
    assert [(row["resolved_start"], row["changes"]["blink"]) for row in planned] == [(1.0, "EYE_CLOSE_HOLD"), (3.0, "EYE_OPEN")]
    with pytest.raises(ValueError, match="requires an active EYE_CLOSE_HOLD"):
        build_blink_overlay_key_schedule([{"event_id": "OPEN", "resolved_start": 1, "changes": {"blink": "EYE_OPEN"}}], fps=24, config=config)
    with pytest.raises(ValueError, match="permits only EYE_OPEN"):
        plan_v2_blinks([_resolved("HOLD", 1, blink="EYE_CLOSE_HOLD"), _resolved("BLINK", 2, blink="SLOW_BLINK")])


def test_v2_blink_schedule_uses_evidenced_per_preset_closure_values():
    config = {"open_value": 0, "presets": {
        "BLINK": {"closure": 7, "close_frames": 1, "hold_frames": 0, "open_frames": 1, "count": 1, "gap_frames": 0},
        "DOUBLE_BLINK": {"closure": 7, "close_frames": 1, "hold_frames": 0, "open_frames": 1, "count": 2, "gap_frames": 1},
        "SLOW_BLINK": {"closure": 8, "close_frames": 1, "hold_frames": 0, "open_frames": 1, "count": 1, "gap_frames": 0},
        "EYE_CLOSE_HOLD": {"closure": 9, "close_frames": 1},
    }}
    for blink, closure in (("BLINK", 7), ("DOUBLE_BLINK", 7), ("SLOW_BLINK", 8), ("EYE_CLOSE_HOLD", 9)):
        keys = build_blink_overlay_key_schedule([{"event_id": blink, "resolved_start": 0, "changes": {"blink": blink}}], fps=24, config=config)
        assert closure in [key["value"] for key in keys]


def test_v2_blink_config_uses_live_evidenced_per_preset_closures():
    import yaml

    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs" / "maya" / "valleygirl.yaml").read_text(encoding="utf-8"))["maya_performative_blink_overlay"]
    assert config["open_value"] == 0
    assert {name: preset["closure"] for name, preset in config["presets"].items()} == {
        "BLINK": 7, "DOUBLE_BLINK": 7, "SLOW_BLINK": 8, "EYE_CLOSE_HOLD": 9,
    }
    assert "closed_value" not in config


def _resolved(event_id, time, actor="ALICE", **changes):
    return {"event_id": event_id, "actor": actor, "resolved_start": time, "changes": changes}


def test_v2_idle_head_is_deterministic_actor_specific_and_never_yaws():
    config = _v2_overlay_config()["idle_head"]
    a = plan_idle_head_drift(actor="A", sound_file="S", speaking_intervals=[(0, 5), (10, 12)], duration_seconds=15, fps=30, config=config)
    b = plan_idle_head_drift(actor="A", sound_file="S", speaking_intervals=[(0, 5), (10, 12)], duration_seconds=15, fps=30, config=config)
    listener = plan_idle_head_drift(actor="B", sound_file="S", speaking_intervals=[], duration_seconds=5, fps=30, config=config)
    assert a == b and listener and any(key["frame"] == 150 and key["rotateX"] == key["rotateZ"] == 0 for key in a)
    assert all(abs(key["rotateX"]) <= .80 and abs(key["rotateZ"]) <= .45 and "rotateY" not in key for key in a + listener)
    assert idle_head_layer_name("JOAN") == "JALITEST_idleHead_JOAN"


def test_v2_idle_regulatory_blinks_are_deterministic_and_feed_existing_blink_schedules():
    config = _v2_overlay_config()["blink"]
    first = inject_idle_regulatory_blinks([], actor="ALICE", sound_file="Seq1_ALICE", duration_seconds=14, fps=30, blink_config=config)
    second = inject_idle_regulatory_blinks([], actor="ALICE", sound_file="Seq1_ALICE", duration_seconds=14, fps=30, blink_config=config)
    assert first == second and first
    assert all(row["changes"] == {"blink": "BLINK"} and row["blink_source"] == "idle_regulatory" for row in first)
    assert all(0 <= row["resolved_start"] < 14 and row["resolved_start"] + 5 / 30 <= 14 for row in first)
    eyelid = build_blink_overlay_key_schedule(first, fps=30, config=config)
    brow = build_blink_brow_companion_key_schedule(first, fps=30, config=config)
    assert len(eyelid) == len(brow) == 4 * len(first)
    assert [key["value"] for key in eyelid[:4]] == [0.0, 7.0, 7.0, 0.0]
    assert [key["value"] for key in brow[:4]] == [0.0, 2.0, 2.0, 0.0]


def test_v2_idle_regulatory_blinks_reset_after_higher_priority_and_hold_release():
    config = _v2_overlay_config()["blink"]
    config["idle_regulatory"] = {"enabled": True, "min_interval_seconds": 3.5, "max_interval_seconds": 3.5, "min_separation_seconds": 0.75}
    semantic = plan_v2_blinks([
        _resolved("HOLD", 1.0, blink="EYE_CLOSE_HOLD"),
        _resolved("OPEN", 7.0, blink="EYE_OPEN"),
        _resolved("DOUBLE", 11.0, blink="DOUBLE_BLINK"),
    ])
    planned = inject_idle_regulatory_blinks(semantic, actor="ALICE", sound_file="Seq1_ALICE", duration_seconds=18, fps=30, blink_config=config)
    idle = [row for row in planned if row["blink_source"] == "idle_regulatory"]
    assert all(not 1.0 <= row["resolved_start"] < 7.0 + 4 / 30 for row in idle)
    assert all(abs(row["resolved_start"] - 11.0) >= 0.75 for row in idle)
    assert any(row["resolved_start"] > 11.0 + 14 / 30 for row in idle)
    assert all(row["resolved_start"] + 5 / 30 <= 18 for row in idle)


def _fixation_gaze(mode, start, end, *, role=None, onset=None):
    event = {"mode": mode, "resolved_time": {"start": start, "end": end}}
    if role is not None: event["timing_role"] = role
    if onset is not None: event["visual_onset"] = onset
    return event


def test_v2_micro_saccade_continuous_triangle_keys_are_literal_additive_deltas():
    config = _v2_overlay_config()["micro_saccade"]
    keys = build_fixation_micro_saccade_key_schedule([{"cluster_id": "A", "start_frame": 100, "end_frame": 170}], config=config)
    assert [(key["frame"], key["x"], key["y"]) for key in keys[:10]] == [(100.0, 0.0, 0.0), (102.0, -.28, .10), (112.0, -.28, .10), (114.0, 0.0, -.18), (124.0, 0.0, -.18), (126.0, .24, .08), (136.0, .24, .08), (138.0, -.28, .10), (148.0, -.28, .10), (150.0, 0.0, -.18)]


def test_v2_micro_saccades_only_use_persistent_gaze_and_exclude_glance():
    config = _v2_overlay_config()["micro_saccade"]
    blink = _v2_overlay_config()["blink"]
    gaze = fixation_gaze_intervals([_fixation_gaze("GAZE", 0, 10)], fps=30, transition_frames=3, duration_seconds=10)
    glance = fixation_gaze_intervals([_fixation_gaze("GLANCE", 0, 2)], fps=30, transition_frames=3, duration_seconds=10)
    assert plan_fixation_micro_saccades(gaze, blink_events=[], actor="ALICE", sound_file="A", duration_seconds=10, fps=30, config=config, blink_config=blink)
    assert plan_fixation_micro_saccades(glance, blink_events=[], actor="ALICE", sound_file="A", duration_seconds=10, fps=30, config=config, blink_config=blink) == []
    for direction in ("UP", "DOWN", "LEFT", "RIGHT"):
        assert plan_fixation_micro_saccades(gaze, blink_events=[], actor="ALICE", sound_file=direction, duration_seconds=10, fps=30, config=config, blink_config=blink)


def test_v2_micro_saccades_continue_under_blinks_but_restart_after_held_closure():
    micro = _v2_overlay_config()["micro_saccade"]
    blink = _v2_overlay_config()["blink"]
    raw = [_fixation_gaze("GAZE", 0, 4), _fixation_gaze("GLANCE", 4, 5, onset=3.9), _fixation_gaze("GAZE", 8, 12)]
    intervals = fixation_gaze_intervals(raw, fps=30, transition_frames=3, duration_seconds=12)
    assert intervals == [(0.0, 3.9), (5.0, 8.0), (8.1, 12)]
    blinks = [{"resolved_start": 1.0, "changes": {"blink": "BLINK"}}, {"resolved_start": 6.0, "changes": {"blink": "EYE_CLOSE_HOLD"}}, {"resolved_start": 7.0, "changes": {"blink": "EYE_OPEN"}}, {"resolved_start": 10.0, "changes": {"blink": "BLINK"}, "blink_source": "idle_regulatory"}]
    runs = plan_fixation_micro_saccades(intervals, blink_events=blinks, actor="ALICE", sound_file="A", duration_seconds=12, fps=30, config=micro, blink_config=blink)
    keys = build_fixation_micro_saccade_key_schedule(runs, config=micro)
    assert any(30 < key["frame"] < 120 and key["x"] != 0 for key in keys)  # ordinary blink does not interrupt
    assert all(not 180 < key["frame"] < 214 for key in keys)  # held closed through release
    assert any(key["frame"] == 214 and key["x"] == 0 for key in keys)


def test_v2_micro_saccade_layer_name_and_plugs_are_separate_from_eye_stare():
    assert micro_saccade_layer_name("ALICE") == "JALITEST_microSaccade_ALICE"
    rig = "|world|ALICE:ROOT"
    plugs = [f"{qualify_rig_control(rig, 'CNT_BOTH_EYES')}.{attribute}" for attribute in ("translateX", "translateY")]
    assert plugs == ["ALICE:CNT_BOTH_EYES.translateX", "ALICE:CNT_BOTH_EYES.translateY"]
    assert all("eyeStare_world" not in plug and not plug.endswith("translateZ") for plug in plugs)


def test_v2_micro_saccade_apply_uses_only_additive_both_eyes_layer():
    cmds = _DualCmds()
    context = {
        "schema_version": "dual_gaze_only_prepared_v1", "fps": 30, "jsync_nodes": {},
        "ALICE": {
            "reference": {"eye_stare_node": "ALICE:eyeStare_world", "both_eyes_node": "ALICE:CNT_BOTH_EYES"}, "keys": [], "gaze_events": 1,
            "layer": "JALITEST_gaze_ALICE", "managed_gaze_plugs": [], "micro_saccade_node": "ALICE:CNT_BOTH_EYES",
            "micro_saccade_layer": "JALITEST_microSaccade_ALICE", "micro_saccade_plugs": ["ALICE:CNT_BOTH_EYES.translateX", "ALICE:CNT_BOTH_EYES.translateY"],
            "micro_saccade_x_attribute": "translateX", "micro_saccade_y_attribute": "translateY",
            "micro_saccade_keys": build_fixation_micro_saccade_key_schedule([{"start_frame": 100, "end_frame": 170}], config=_v2_overlay_config()["micro_saccade"]),
        },
    }
    result = apply_dual_gaze_only_artifacts(prepared_context=context, cmds_module=cmds)
    micro = [call for call in cmds.calls if call[0] == "setKeyframe" and call[1][0] == "ALICE:CNT_BOTH_EYES"]
    assert result["ALICE"]["micro_saccade_plugs"] == ["ALICE:CNT_BOTH_EYES.translateX", "ALICE:CNT_BOTH_EYES.translateY"]
    assert len(micro) == 2 * len(context["ALICE"]["micro_saccade_keys"]) and {call[2]["animLayer"] for call in micro} == {"JALITEST_microSaccade_ALICE"}
    assert not any(call[0] == "setKeyframe" and "eyeStare_world" in call[1][0] for call in cmds.calls)
    assert ("JALITEST_microSaccade_ALICE",) in {call[1] for call in cmds.calls if call[0] == "animLayer" and call[2].get("override") is False}


def test_v2_gaze_prepare_keeps_micro_saccades_in_a_separate_runtime_section(monkeypatch, tmp_path):
    import animation_apply_runner as runner

    artifacts = {"characters": {}}
    for actor, gaze in (("ALICE", "GAZE-BOB"), ("BOB", "GAZE-ALICE")):
        path = tmp_path / f"{actor}.json"
        path.write_text(json.dumps({"initial_state": {"state": {"gaze": gaze}}, "events": []}), encoding="utf-8")
        artifacts["characters"][actor] = {"resolved_sparse_events": str(path)}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "dual_animation_manifest_v2", "characters": ["ALICE", "BOB"], "fps": 30, "shared_duration_seconds": 12, "character_runtime_mapping": {"ALICE": {"script_name": "ALICE", "sound_file": "A"}, "BOB": {"script_name": "BOB", "sound_file": "B"}}, "artifacts": artifacts}), encoding="utf-8")
    monkeypatch.setattr(runner, "resolve_jsync_for_character", lambda rig, *_args, **_kwargs: f"{rig}|jSync")
    monkeypatch.setattr(runner, "capture_character_gaze_reference", lambda rig, **_kwargs: {"eye_stare_node": qualify_rig_control(rig, "eyeStare_world"), "both_eyes_node": qualify_rig_control(rig, "CNT_BOTH_EYES"), "eye_stare_translate": [0, 0, 9], "both_eyes_translate": [0, 0]})
    mappings = {"ALICE": {"maya_node": "|ALICE:ROOT", "gaze_targets": {"BOB": {"eye_stare_translate": [1, 2, 3]}}}, "BOB": {"maya_node": "|BOB:ROOT", "gaze_targets": {"ALICE": {"eye_stare_translate": [4, 5, 6]}}}}
    prepared = prepare_dual_v2_gaze_only_artifacts(manifest_path=manifest, character_mappings=mappings, cmds_module=_ListenerCmds())
    alice = prepared["ALICE"]
    assert prepared["warnings"] == []
    assert alice["micro_saccade_layer"] == "JALITEST_microSaccade_ALICE"
    assert alice["micro_saccade_plugs"] == ["ALICE:CNT_BOTH_EYES.translateX", "ALICE:CNT_BOTH_EYES.translateY"]
    assert alice["micro_saccade_keys"] and all("eyeStare_world" not in plug for plug in alice["micro_saccade_plugs"])
    assert all(key["x"] in {0.0, -.28, .24} and key["y"] in {0.0, .10, -.18, .08} for key in alice["micro_saccade_keys"])


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


def test_v2_affect_regulatory_blinks_are_centered_in_configured_affect_transitions():
    blink_config = _v2_overlay_config()["blink"]
    speaker = _resolved("S", 2, affect="Watchful-82")
    speaker["timing_role"] = "SPEAK_ONSET"
    listener = _resolved("L", 2, affect="Watchful-82")
    listener["timing_role"] = "LISTEN_REACTION"
    common = {"initial_state": {"affect": "Nervous-78", "gaze": "GAZE-BOB"}, "fps": 30, "affect_transition_frames": 12, "blink_config": blink_config}
    speaker_plan = plan_v2_blinks([speaker], **common)
    listener_plan = plan_v2_blinks([listener], **common)
    assert speaker_plan[0]["resolved_start"] == listener_plan[0]["resolved_start"] == 2.0
    assert speaker_plan[0]["visual_start_frame"] == 52.0
    assert listener_plan[0]["visual_start_frame"] == 64.0
    speaker_keys = build_blink_overlay_key_schedule(speaker_plan, fps=30, config=blink_config)
    listener_keys = build_blink_overlay_key_schedule(listener_plan, fps=30, config=blink_config)
    assert speaker_keys[1]["frame"] == 54.0
    assert listener_keys[1]["frame"] == 66.0


def test_v2_only_affect_regulatory_blinks_receive_visual_offsets():
    blink_config = _v2_overlay_config()["blink"]
    affect = _resolved("A", 2, affect="Watchful-82")
    affect["timing_role"] = "LISTEN_REACTION"
    gaze = _resolved("G", 3, gaze="GAZE-DOWN")
    explicit = _resolved("E", 4, blink="SLOW_BLINK")
    planned = plan_v2_blinks(
        [affect, gaze, explicit], initial_state={"affect": "Nervous-78", "gaze": "GAZE-BOB"},
        fps=30, affect_transition_frames=12, blink_config=blink_config,
    )
    by_source = {row["blink_source"]: row for row in planned}
    assert by_source["affect_regulatory"]["visual_start_frame"] == 64.0
    assert "visual_start_frame" not in by_source["gaze_regulatory"]
    assert "visual_start_frame" not in by_source["explicit"]


def test_v2_intensity_only_affect_transition_is_smooth_without_regulatory_blink():
    nervous_65 = user_pose_for_mask("Nervous-65")
    nervous_78 = user_pose_for_mask("Nervous-78")
    keys = build_v2_listener_mask_key_schedule([
        {"phrase_id": "INITIAL_STATE", "start": 0, "pose": nervous_65, "boundary_kind": "INITIAL_STATE"},
        {"phrase_id": "intensity", "start": 2, "pose": nervous_78, "boundary_kind": "affect", "timing_role": "LISTEN_REACTION"},
    ], fps=30)
    event = _resolved("I", 2, affect="Nervous-78")
    event["timing_role"] = "LISTEN_REACTION"
    assert [key["frame"] for key in keys] == [0.0, 60.0, 72.0]
    assert plan_v2_blinks([event], initial_state={"affect": "Nervous-65", "gaze": "GAZE-BOB"}) == []


def test_v2_close_affect_boundaries_clamp_post_anchor_transition_without_neutral():
    nervous = {"value": 1.0}; watchful = {"value": 2.0}; dislike = {"value": 3.0}
    keys = build_v2_listener_mask_key_schedule([
        {"phrase_id": "INITIAL_STATE", "start": 0, "pose": nervous, "boundary_kind": "INITIAL_STATE"},
        {"phrase_id": "first", "start": 2.0, "pose": watchful, "boundary_kind": "affect", "timing_role": "LISTEN_REACTION"},
        {"phrase_id": "second", "start": 2.2, "pose": dislike, "boundary_kind": "affect", "timing_role": "LISTEN_REACTION"},
    ], fps=30)
    assert [key["frame"] for key in keys] == sorted(key["frame"] for key in keys)
    assert not any(key["pose"] == nervous and key["frame"] > 66.0 for key in keys)
    assert {tuple(key["pose"].items()) for key in keys} <= {tuple(nervous.items()), tuple(watchful.items()), tuple(dislike.items())}


def test_v2_regulatory_blink_planner_is_actor_independent_and_first_state_is_initialization():
    alice = plan_v2_blinks([_resolved("A1", 0, gaze="GAZE-BOB"), _resolved("A2", 1, gaze="GAZE-DOWN")])
    bob = plan_v2_blinks([_resolved("B1", 0, actor="BOB", affect="Nervous-60")])
    assert len(alice) == 1 and alice[0]["actor"] == "ALICE"
    assert bob == []
    glance = plan_v2_blinks([_resolved("G1", 0, gaze="GAZE-BOB"), _resolved("G2", 1, gaze="GLANCE-DOWN"), _resolved("G3", 2, gaze="GAZE-BOB")])
    assert [(row["resolved_start"], row["blink_source"]) for row in glance] == [(1.0, "gaze_regulatory")]
    from_initial = plan_v2_blinks(
        [_resolved("I1", 1, gaze="GAZE-DOWN", affect="Watchful-100")],
        initial_state={"gaze": "GAZE-BOB", "affect": "Watchful-85", "head": "HEAD-NONE"},
    )
    assert len(from_initial) == 1 and from_initial[0]["blink_source"] == "gaze_regulatory"


def test_v2_overlay_apply_uses_owned_additive_layers_and_user_blink_only():
    cmds = _ListenerCmds()
    context = {"schema_version": "dual_v2_head_blink_prepared_v1", "actors": {
        "ALICE": {
            "head_layer": head_layer_name("ALICE"), "blink_layer": blink_layer_name("ALICE"),
            "head_plugs": ["ALICE:jNeck_ctl.rotateX", "ALICE:jNeck_ctl.rotateY", "ALICE:jNeck_ctl.rotateZ"],
            "blink_plugs": ["ALICE:usr_blink.LidDown"], "blink_brow_plugs": ["ALICE:usr_BrowInDown.BrowDown"], "facs_plug": "ALICE:FACSMaster.FACS_animationSource", "facs_add_index": 2,
            "head_keys": [{"frame": 10, "values": {"rotateX": 3, "rotateY": 0, "rotateZ": 0}}],
            "blink_keys": [{"frame": 12, "value": 1}],
            "blink_brow_keys": [{"frame": 12, "value": 0}, {"frame": 14, "value": 2}, {"frame": 16, "value": 0}],
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
    assert any(call[2].get("attribute") == "ALICE:usr_BrowInDown.BrowDown" for call in layer_calls)
    keyed = [call for call in cmds.calls if call[0] == "setKeyframe"]
    assert all("jNeck_ctl" in call[1][0] or "usr_blink" in call[1][0] or "usr_BrowInDown" in call[1][0] for call in keyed)
    brow_keys = [call for call in keyed if "usr_BrowInDown" in call[1][0]]
    assert [call[2]["value"] for call in brow_keys] == [0, 2, 0]
    assert all(call[2]["attribute"] == "BrowDown" for call in brow_keys)
    assert result["ALICE"]["jali_calculate_blinks_disabled"] is True


def test_v2_blink_ownership_diagnostic_allows_native_vendor_curves_but_rejects_actual_conflicts():
    context = {"schema_version": "dual_v2_head_blink_prepared_v1", "actors": {"ALICE": {
        "jsync": "ALICE:jSync", "blink_layer": "JALITEST_blink_ALICE",
        "vendor_blink_plug": "ALICE:LIDS_jSync_plusMinus.Down_upLids_jSync",
        "blink_plugs": ["ALICE:usr_blink.LidDown"],
    }}}
    class Cmds:
        def __init__(self, *, calculate_blinks=False, vendor_curves=(), user_curves=("jalitestBlinkCurve",)):
            self.calculate_blinks = calculate_blinks
            self.vendor_curves = vendor_curves
            self.user_curves = user_curves
        def getAttr(self, _plug): return self.calculate_blinks
        def objExists(self, _node): return True
        def animLayer(self, _layer, **_kwargs): return ["jalitestBlinkCurve"]
        def listConnections(self, plug, **_kwargs):
            return self.vendor_curves if "LIDS_jSync" in plug else self.user_curves
    assert diagnose_v2_blink_ownership(prepared_context=context, cmds_module=Cmds())["passed"] is True
    native = diagnose_v2_blink_ownership(
        prepared_context=context,
        cmds_module=Cmds(vendor_curves=("jSync1_au41_LidDwnA",)),
    )
    assert native["passed"] is True
    assert native["actors"]["ALICE"]["vendor_anim_curves"] == ["jSync1_au41_LidDwnA"]
    with pytest.raises(RuntimeError, match="calculate_blinks is not False"):
        diagnose_v2_blink_ownership(prepared_context=context, cmds_module=Cmds(calculate_blinks=True))
    with pytest.raises(RuntimeError, match="User blink controls have curves outside"):
        diagnose_v2_blink_ownership(prepared_context=context, cmds_module=Cmds(user_curves=("foreignCurve",)))


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
    assert reference["both_eyes_translate"] == [0.0, 0.0]
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
    assert prepared["A"]["gaze_reference"]["both_eyes_translate"] == [0.0, 0.0]
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
    assert schedule[1]["eye_stare"] == [9,8,7] and schedule[1]["eyes"] == [0.0,0.0]


def test_first_gaze_at_timeline_start_initializes_directly_without_neutral_transition():
    raw = [{"channel": "gaze", "value": "GAZE-B", "resolved_time": {"start": 0, "end": 10}}]
    schedule = build_dual_gaze_schedule(adapt_dual_gaze_events(raw), neutral_position=[9, 9, 9], neutral_eyes=[4, 5], target_positions={"B": [1, 2, 3]})
    keys = build_dual_gaze_key_schedule(schedule, fps=1, transition_frames=3)
    assert keys == [{"frame": 0.0, "eye_stare": [1, 2, 3], "eyes": [0.0, 0.0]}]


def test_first_avert_at_timeline_start_initializes_its_complete_state():
    raw = [{"channel": "gaze", "value": "AVERT-DOWN", "resolved_time": {"start": 0, "end": 10}}]
    schedule = build_dual_gaze_schedule(adapt_dual_gaze_events(raw), neutral_position=[9, 8, 7], neutral_eyes=[1, 2], target_positions={})
    keys = build_dual_gaze_key_schedule(schedule, fps=1, transition_frames=3)
    assert keys == [{"frame": 0.0, "eye_stare": [9, 8, 7], "eyes": [1, -3.0]}]


def test_first_gaze_after_timeline_start_keeps_neutral_then_uses_transition():
    raw = [{"channel": "gaze", "value": "GAZE-B", "resolved_time": {"start": 2, "end": 10}}]
    schedule = build_dual_gaze_schedule(adapt_dual_gaze_events(raw), neutral_position=[9, 9, 9], neutral_eyes=[4, 5], target_positions={"B": [1, 2, 3]})
    keys = build_dual_gaze_key_schedule(schedule, fps=1, transition_frames=3)
    assert keys == [{"frame": 2.0, "eye_stare": [9, 9, 9], "eyes": [4, 5]}, {"frame": 5.0, "eye_stare": [1, 2, 3], "eyes": [0.0, 0.0]}]


def test_time_zero_persistent_gaze_transition_preserves_initial_state_before_arrival():
    events = [
        {"id": "INITIAL:AGNES", "mode": "GAZE", "target": "HAWK", "timing_role": "INITIAL_STATE", "resolved_time": {"start": 0.0, "end": 1.0}},
        {"id": "E001", "mode": "GAZE", "target": "WILL", "timing_role": "SPEAK_ONSET", "resolved_time": {"start": 0.0, "end": 1.0}},
    ]
    schedule = build_dual_gaze_schedule(
        events, neutral_position=[0, 0, 0], neutral_eyes=[0, 0],
        target_positions={"HAWK": [1, 2, 3], "WILL": [4, 5, 6]},
    )
    assert [state["start"] for state in schedule] == [0.0, 0.0]
    keys = build_dual_gaze_key_schedule(schedule, fps=30, transition_frames=3)
    assert keys == [
        {"frame": 0.0, "eye_stare": [1, 2, 3], "eyes": [0.0, 0.0]},
        {"frame": 3.0, "eye_stare": [4, 5, 6], "eyes": [0.0, 0.0]},
    ]


def test_time_zero_persistent_gaze_matching_initial_remains_a_noop():
    events = [
        {"id": "INITIAL:AGNES", "mode": "GAZE", "target": "WILL", "timing_role": "INITIAL_STATE", "resolved_time": {"start": 0.0, "end": 1.0}},
        {"id": "E001", "mode": "GAZE", "target": "WILL", "timing_role": "SPEAK_ONSET", "resolved_time": {"start": 0.0, "end": 1.0}},
    ]
    schedule = build_dual_gaze_schedule(
        events, neutral_position=[0, 0, 0], neutral_eyes=[0, 0], target_positions={"WILL": [4, 5, 6]},
    )
    assert build_dual_gaze_key_schedule(schedule, fps=30, transition_frames=3) == [
        {"frame": 0.0, "eye_stare": [4, 5, 6], "eyes": [0.0, 0.0]},
    ]


def test_later_persistent_gaze_keeps_existing_speaker_preroll_schedule():
    events = [
        {"id": "INITIAL:AGNES", "mode": "GAZE", "target": "HAWK", "timing_role": "INITIAL_STATE", "resolved_time": {"start": 0.0, "end": 4.0}},
        {"id": "E001", "mode": "GAZE", "target": "WILL", "timing_role": "SPEAK_ONSET", "resolved_time": {"start": 4.0, "end": 5.0}},
    ]
    schedule = build_dual_gaze_schedule(
        events, neutral_position=[0, 0, 0], neutral_eyes=[0, 0],
        target_positions={"HAWK": [1, 2, 3], "WILL": [4, 5, 6]},
    )
    assert [key["frame"] for key in build_dual_gaze_key_schedule(schedule, fps=30, transition_frames=3)] == [0.0, 117.0, 120.0]


def test_time_zero_glance_keeps_initial_persistent_return_state():
    events = [
        {"id": "INITIAL:AGNES", "mode": "GAZE", "target": "HAWK", "timing_role": "INITIAL_STATE", "resolved_time": {"start": 0.0, "end": 1.0}},
        {"id": "E001", "mode": "GLANCE", "target": "DOWN", "timing_role": "LISTEN_REACTION", "resolved_time": {"start": 0.0, "end": 1.0}},
    ]
    schedule = build_dual_gaze_schedule(
        events, neutral_position=[0, 0, 0], neutral_eyes=[0, 0], target_positions={"HAWK": [1, 2, 3]},
    )
    keys = build_dual_gaze_key_schedule(schedule, fps=30, transition_frames=3, glance_hold_seconds=.5)
    assert schedule[1]["return_state"] == {"eye_stare": [1, 2, 3], "eyes": [0.0, 0.0]}
    assert keys[-1] == {"frame": 30.0, "eye_stare": [1, 2, 3], "eyes": [0.0, 0.0]}


def test_genuine_non_boundary_conflicting_gaze_keys_still_fail():
    schedule = [
        {"event": {"mode": "GAZE", "timing_role": "LISTEN_REACTION"}, "start": 2.0, "end": 5.0, "previous_state": {"eye_stare": [0, 0, 0], "eyes": [0, 0]}, "eye_stare": [1, 0, 0], "eyes": [0, 0]},
        {"event": {"mode": "GAZE", "timing_role": "LISTEN_REACTION"}, "start": 2.0, "end": 5.0, "previous_state": {"eye_stare": [2, 0, 0], "eyes": [0, 0]}, "eye_stare": [3, 0, 0], "eyes": [0, 0]},
    ]
    with pytest.raises(ValueError, match="Conflicting gaze keys at frame 60.0"):
        build_dual_gaze_key_schedule(schedule, fps=30, transition_frames=3)


def test_v2_gaze_role_aware_key_realization_keeps_semantic_boundary_exact():
    speaker = [{"event": {"mode": "GAZE", "timing_role": "SPEAK_ONSET"}, "start": 2.0, "end": 5.0, "previous_state": {"eye_stare": [0, 0, 9], "eyes": [0, 0]}, "eye_stare": [1, 2, 3], "eyes": [0, 0]}]
    listener = [{"event": {"mode": "GAZE", "timing_role": "LISTEN_REACTION"}, "start": 2.0, "end": 5.0, "previous_state": {"eye_stare": [0, 0, 9], "eyes": [0, 0]}, "eye_stare": [1, 2, 3], "eyes": [0, 0]}]
    assert [key["frame"] for key in build_dual_gaze_key_schedule(speaker, fps=24, transition_frames=4)] == [44.0, 48.0]
    assert [key["frame"] for key in build_dual_gaze_key_schedule(listener, fps=24, transition_frames=4)] == [48.0, 52.0]


def test_v2_glance_role_aware_key_realization_uses_causal_listener_and_speaker_onset():
    state = {"eye_stare": [1, 2, 3], "eyes": [0, 0]}
    previous = {"eye_stare": [0, 0, 9], "eyes": [0, 0]}
    speaker = [{"event": {"mode": "GLANCE", "timing_role": "SPEAK_ONSET"}, "start": 2.0, "end": 2.625, "previous_state": previous, **state, "return_state": previous}]
    listener = [{"event": {"mode": "GLANCE", "timing_role": "LISTEN_REACTION"}, "start": 2.0, "end": 2.75, "previous_state": previous, **state, "return_state": previous}]
    assert [key["frame"] for key in build_dual_gaze_key_schedule(speaker, fps=24, glance_transition_frames=3, glance_hold_seconds=.5)] == [45.0, 48.0, 60.0, 63.0]
    assert [key["frame"] for key in build_dual_gaze_key_schedule(listener, fps=24, glance_transition_frames=3, glance_hold_seconds=.5)] == [48.0, 51.0, 63.0, 66.0]


def test_glance_is_clamped_by_following_speaker_visual_lead_in_without_key_conflict():
    events = [
        {"id": "G1", "mode": "GLANCE", "target": "B", "timing_role": "LISTEN_REACTION", "resolved_time": {"start": 2.0, "end": 3.0}},
        {"id": "G2", "mode": "GAZE", "target": "A", "timing_role": "SPEAK_ONSET", "visual_onset": 2.875, "resolved_time": {"start": 3.0, "end": 4.0}},
    ]
    schedule = build_dual_gaze_schedule(events, neutral_position=[0, 0, 0], neutral_eyes=[0, 0], target_positions={"A": [1, 1, 1], "B": [2, 2, 2]})
    assert schedule[0]["end"] == 2.875
    keys = build_dual_gaze_key_schedule(schedule, fps=24, transition_frames=3, glance_transition_frames=3, glance_hold_seconds=.5)
    keyed = {(key["frame"], tuple(key["eye_stare"]), tuple(key["eyes"])) for key in keys}
    assert any(key[0] == 69.0 for key in keyed)
    by_frame = {}
    for key in keys:
        by_frame.setdefault(key["frame"], set()).add((tuple(key["eye_stare"]), tuple(key["eyes"])))
    assert all(len(states) == 1 for states in by_frame.values())


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
        {"frame": 48.0, "eye_stare": [1, 1, 1], "eyes": [0.0, 0.0]},
        {"frame": 51.0, "eye_stare": [2, 2, 2], "eyes": [0.0, 0.0]},
        {"frame": 69.0, "eye_stare": [2, 2, 2], "eyes": [0.0, 0.0]},
        {"frame": 72.0, "eye_stare": [1, 1, 1], "eyes": [0.0, 0.0]},
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


def test_listener_glance_exact_fit_at_non_integer_frame_accepts_float_roundoff():
    start_seconds = 11.638749
    fps = 30.0
    previous = {"eye_stare": [0, 0, 9], "eyes": [0, 0]}
    schedule = [{
        "event": {"id": "E002", "mode": "GLANCE", "timing_role": "LISTEN_REACTION"},
        "start": start_seconds,
        "end": start_seconds + .5 + 3 / fps + 3 / fps,
        "previous_state": previous,
        "eye_stare": [1, 2, 3], "eyes": [0, 0], "return_state": previous,
    }]

    keys = build_dual_gaze_key_schedule(
        schedule, fps=fps, glance_transition_frames=3, glance_hold_seconds=.5
    )

    target_keys = [key for key in keys if key["eye_stare"] == [1, 2, 3]]
    assert target_keys[1]["frame"] - target_keys[0]["frame"] >= 15 - 1e-6


def test_speaker_glance_exact_fit_at_non_integer_frame_accepts_float_roundoff():
    start_seconds = 11.638749
    fps = 30.0
    previous = {"eye_stare": [0, 0, 9], "eyes": [0, 0]}
    schedule = [{
        "event": {"id": "E003", "mode": "GLANCE", "timing_role": "SPEAK_ONSET"},
        "start": start_seconds,
        "end": start_seconds + .5 + 3 / fps,
        "previous_state": previous,
        "eye_stare": [1, 2, 3], "eyes": [0, 0], "return_state": previous,
    }]

    keys = build_dual_gaze_key_schedule(
        schedule, fps=fps, glance_transition_frames=3, glance_hold_seconds=.5
    )

    target_keys = [key for key in keys if key["eye_stare"] == [1, 2, 3]]
    assert target_keys[1]["frame"] - target_keys[0]["frame"] >= 15 - 1e-6


def test_listener_glance_short_by_a_tenth_frame_still_fails():
    start_seconds = 11.638749
    fps = 30.0
    previous = {"eye_stare": [0, 0, 9], "eyes": [0, 0]}
    schedule = [{
        "event": {"id": "SHORT", "mode": "GLANCE", "timing_role": "LISTEN_REACTION"},
        "start": start_seconds,
        "end": start_seconds + (21 - .1) / fps,
        "previous_state": previous,
        "eye_stare": [1, 2, 3], "eyes": [0, 0], "return_state": previous,
    }]

    with pytest.raises(ValueError, match="available hold 14.900000"):
        build_dual_gaze_key_schedule(
            schedule, fps=fps, glance_transition_frames=3, glance_hold_seconds=.5
        )


def test_visual_onset_collision_that_truncates_glance_below_minimum_still_fails():
    start_seconds = 11.638749
    fps = 30.0
    events = [
        {"id": "G1", "mode": "GLANCE", "target": "B", "timing_role": "LISTEN_REACTION", "resolved_time": {"start": start_seconds, "end": start_seconds + .7}},
        {"id": "G2", "mode": "GAZE", "target": "A", "timing_role": "SPEAK_ONSET", "visual_onset": start_seconds + 20.9 / fps, "resolved_time": {"start": start_seconds + 1, "end": start_seconds + 2}},
    ]
    schedule = build_dual_gaze_schedule(
        events, neutral_position=[0, 0, 0], neutral_eyes=[0, 0],
        target_positions={"A": [1, 1, 1], "B": [2, 2, 2]},
    )

    with pytest.raises(ValueError, match="GLANCE interval is too short"):
        build_dual_gaze_key_schedule(
            schedule, fps=fps, glance_transition_frames=3, glance_hold_seconds=.5
        )


def test_short_glance_adapter_preserves_e034_with_reduced_hold_and_warning():
    fps = 30.0
    events = [
        {"id": "INITIAL_STATE", "mode": "GAZE", "target": "AGNES", "timing_role": "INITIAL_STATE", "resolved_time": {"start": 0.0, "end": 105.820877}},
        {"id": "E034", "mode": "GLANCE", "target": "DOWN", "timing_role": "LISTEN_REACTION", "resolved_time": {"start": 105.820877, "end": 106.520877}},
        {"id": "E037", "mode": "GAZE", "target": "HAMNET", "timing_role": "LISTEN_REACTION", "visual_onset": 106.300880, "resolved_time": {"start": 106.300880, "end": 108.0}},
    ]
    schedule = build_dual_gaze_schedule(
        events, neutral_position=[0, 0, 9], neutral_eyes=[0, 0],
        target_positions={"AGNES": [1, 0, 9], "HAMNET": [2, 0, 9]},
    )

    adapted, warnings = adapt_short_glance_schedule(
        schedule, fps=fps, glance_transition_frames=3, glance_hold_seconds=.5,
    )
    keys = build_dual_gaze_key_schedule(
        adapted, fps=fps, glance_transition_frames=3, glance_hold_seconds=.5,
        allow_shortened_glance=True,
    )

    down_keys = [key for key in keys if key["eyes"] == [0.0, -5.0]]
    assert down_keys[1]["frame"] - down_keys[0]["frame"] == pytest.approx(8.40009)
    assert warnings == [
        "E034: shortened GLANCE hold from 15 to 8.400090 frames before E037."
    ]


def test_short_glance_adapter_drops_motion_that_cannot_hold_one_frame():
    events = [
        {"id": "INITIAL_STATE", "mode": "GAZE", "target": "A", "timing_role": "INITIAL_STATE", "resolved_time": {"start": 0.0, "end": 2.0}},
        {"id": "SHORT", "mode": "GLANCE", "target": "DOWN", "timing_role": "LISTEN_REACTION", "resolved_time": {"start": 2.0, "end": 2.7}},
        {"id": "NEXT", "mode": "GAZE", "target": "B", "timing_role": "LISTEN_REACTION", "visual_onset": 2.21, "resolved_time": {"start": 2.21, "end": 4.0}},
    ]
    schedule = build_dual_gaze_schedule(
        events, neutral_position=[0, 0, 9], neutral_eyes=[0, 0],
        target_positions={"A": [1, 0, 9], "B": [2, 0, 9]},
    )

    adapted, warnings = adapt_short_glance_schedule(
        schedule, fps=30, glance_transition_frames=3, glance_hold_seconds=.5,
    )
    keys = build_dual_gaze_key_schedule(
        adapted, fps=30, glance_transition_frames=3, glance_hold_seconds=.5,
        allow_shortened_glance=True,
    )

    assert [state["event"]["id"] for state in adapted] == ["INITIAL_STATE", "NEXT"]
    assert not any(key["eyes"] == [0.0, -5.0] for key in keys)
    assert warnings == [
        "SHORT: dropped GLANCE because only 0.300000 hold frames were available before NEXT; at least 1 frame is required."
    ]


def test_short_glance_adapter_leaves_full_hold_unchanged_without_warning():
    events = [{"id": "G1", "mode": "GLANCE", "target": "DOWN", "timing_role": "LISTEN_REACTION", "resolved_time": {"start": 2.0, "end": 2.7}}]
    schedule = build_dual_gaze_schedule(
        events, neutral_position=[0, 0, 9], neutral_eyes=[0, 0], target_positions={},
    )
    adapted, warnings = adapt_short_glance_schedule(
        schedule, fps=30, glance_transition_frames=3, glance_hold_seconds=.5,
    )
    assert adapted == schedule
    assert warnings == []


def test_short_glance_adapter_reports_clip_end_truncation():
    events = [{"id": "LAST", "mode": "GLANCE", "target": "DOWN", "timing_role": "LISTEN_REACTION", "resolved_time": {"start": 9.9, "end": 10.0}}]
    schedule = build_dual_gaze_schedule(
        events, neutral_position=[0, 0, 9], neutral_eyes=[0, 0], target_positions={},
    )
    adapted, warnings = adapt_short_glance_schedule(
        schedule, fps=30, glance_transition_frames=3, glance_hold_seconds=.5,
    )
    assert adapted == []
    assert warnings == [
        "LAST: dropped GLANCE because only 0.000000 hold frames were available before clip end; at least 1 frame is required."
    ]


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
