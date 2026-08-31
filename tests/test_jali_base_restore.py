from __future__ import annotations

from pathlib import Path
import sys

import pytest


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from animation_apply_runner import (  # noqa: E402
    capture_dual_jali_base,
    capture_dual_jali_base_if_absent,
    restore_dual_jali_base,
)


_ATTRS = ("calculate_paralinguals", "paralingual_bearing", "paralingual_intensity", "calculate_expression", "expression_source", "expression_strength", "override_annotation", "calculate_blinks")


class _Cmds:
    def __init__(self, source_dir: Path):
        self.calls: list[tuple[str, tuple, dict]] = []; self.selection = ["|camera"]
        self.nodes = {"|A:ROOT", "|B:ROOT", "|A:ROOT|jSyncA", "|B:ROOT|jSyncB", "JALITEST_listenerMask_A", "JALITEST_listenerMask_B", "JALITEST_gaze_A", "JALITEST_gaze_B", "JALITEST_head_A", "JALITEST_head_B", "JALITEST_blink_A", "JALITEST_blink_B", "JALITEST_microSaccade_A", "JALITEST_microSaccade_B", "animator_layer"}
        self.values: dict[str, object] = {}
        for alias, jsync, sound, source, facs in (("A", "|A:ROOT|jSyncA", "SA", source_dir / "SA.txt", "A:FACSMaster"), ("B", "|B:ROOT|jSyncB", "SB", source_dir / "SB.txt", "B:FACSMaster")):
            self.values.update({f"{jsync}.sound_file": sound, f"{jsync}.text_input_path": str(source_dir), f"{jsync}.sound_input_path": f"{alias}/sound", f"{jsync}.output_path": f"{alias}/output", f"{jsync}.transcript": f"original {alias}", f"{facs}.FACS_animationSource": 1})
            for index, attr in enumerate(_ATTRS): self.values[f"{jsync}.{attr}"] = index + (0 if alias == "A" else 100)
    def objExists(self, item): return item in self.nodes or item in self.values
    def ls(self, **kwargs):
        if kwargs.get("type") == "jSync": return ["|A:ROOT|jSyncA", "|B:ROOT|jSyncB"]
        return list(self.selection) if kwargs.get("selection") else []
    def getAttr(self, plug): return self.values[plug]
    def setAttr(self, plug, value, **kwargs): self.values[plug] = value; self.calls.append(("setAttr", (plug, value), kwargs))
    def delete(self, node): self.nodes.discard(node); self.calls.append(("delete", (node,), {}))
    def select(self, items=None, **kwargs): self.selection = [] if kwargs.get("clear") else list(items or []); self.calls.append(("select", tuple(self.selection), kwargs))


class _Mel:
    def __init__(self): self.calls: list[str] = []
    def eval(self, command): self.calls.append(command); return 1 if command.startswith("exists") else None


def _mappings():
    return {"A": {"script_name": "AGNES", "maya_node": "|A:ROOT"}, "B": {"script_name": "WILL", "maya_node": "|B:ROOT"}}


def _baseline(tmp_path):
    (tmp_path / "SA.txt").write_text("A"); (tmp_path / "SB.txt").write_text("B")
    cmds = _Cmds(tmp_path)
    return cmds, capture_dual_jali_base(character_mappings=_mappings(), cmds_module=cmds)


def test_baseline_is_actor_specific_and_first_capture_is_immutable(tmp_path):
    cmds, baseline = _baseline(tmp_path)
    assert baseline["actors"]["A"]["jsync"].endswith("jSyncA")
    assert baseline["actors"]["B"]["jsync"].endswith("jSyncB")
    cmds.values["|A:ROOT|jSyncA.transcript"] = "tagged A"
    assert capture_dual_jali_base_if_absent(baseline, character_mappings=_mappings(), cmds_module=cmds) is baseline
    assert baseline["actors"]["A"]["transcript"] == "original A"


def test_baseline_capture_uses_mapping_expected_sound_when_old_jsync_exists(tmp_path):
    cmds, _baseline_value = _baseline(tmp_path)
    old = "|A:ROOT|old|jSyncOld"
    cmds.nodes.add(old)
    cmds.values[f"{old}.sound_file"] = "OLD"
    original_ls = cmds.ls
    cmds.ls = lambda **kwargs: (["|A:ROOT|jSyncA", old, "|B:ROOT|jSyncB"] if kwargs.get("type") == "jSync" else original_ls(**kwargs))
    mappings = _mappings()
    mappings["A"]["sound_file"] = "SA"
    mappings["B"]["sound_file"] = "SB"
    baseline = capture_dual_jali_base(character_mappings=mappings, cmds_module=cmds)
    assert baseline["actors"]["A"]["jsync"] == "|A:ROOT|jSyncA"
    assert baseline["actors"]["B"]["jsync"] == "|B:ROOT|jSyncB"


def test_restore_preflights_b_before_mutating_a(tmp_path):
    cmds, baseline = _baseline(tmp_path); mel = _Mel()
    cmds.nodes.discard("|B:ROOT|jSyncB")
    with pytest.raises(RuntimeError, match="original jSync is missing"):
        restore_dual_jali_base(baseline=baseline, character_mappings=_mappings(), cmds_module=cmds, mel_module=mel)
    assert not cmds.calls


def test_restore_removes_only_owned_layers_and_restores_live_jali_state(tmp_path):
    cmds, baseline = _baseline(tmp_path); mel = _Mel()
    for alias in ("A", "B"):
        jsync = baseline["actors"][alias]["jsync"]
        cmds.values[f"{jsync}.transcript"] = "staged"
        cmds.values[f"{jsync}.calculate_paralinguals"] = 999
        cmds.values[f"{jsync}.calculate_blinks"] = False
        cmds.values[f"{('A' if alias == 'A' else 'B')}:FACSMaster.FACS_animationSource"] = 2
    result = restore_dual_jali_base(baseline=baseline, character_mappings=_mappings(), cmds_module=cmds, mel_module=mel)
    assert result["jsync_preserved"] is True
    assert "animator_layer" in cmds.nodes
    assert not any(layer in cmds.nodes for layer in result["removed_layers"])
    assert cmds.values["|A:ROOT|jSyncA.transcript"] == "original A"
    assert cmds.values["|B:ROOT|jSyncB.calculate_paralinguals"] == 100
    assert cmds.values["|A:ROOT|jSyncA.calculate_blinks"] == 7
    assert cmds.values["|B:ROOT|jSyncB.calculate_blinks"] == 107
    assert cmds.values["A:FACSMaster.FACS_animationSource"] == 1
    assert [call for call in mel.calls if call.startswith("realign_node ")] == ['realign_node "jSyncA"', 'realign_node "jSyncB"']
    assert not any("resurrect" in call for call in mel.calls)
    assert cmds.selection == ["|camera"]


def test_generate_restore_generate_can_repeat_without_replacing_baseline(tmp_path):
    cmds, baseline = _baseline(tmp_path); mel = _Mel()
    for _ in range(2):
        cmds.values["|A:ROOT|jSyncA.transcript"] = "tagged"
        cmds.values["|A:ROOT|jSyncA.calculate_blinks"] = False
        restore_dual_jali_base(baseline=baseline, character_mappings=_mappings(), cmds_module=cmds, mel_module=mel)
        assert cmds.values["|A:ROOT|jSyncA.transcript"] == "original A"
        assert cmds.values["|A:ROOT|jSyncA.calculate_blinks"] == 7
    assert len([call for call in mel.calls if call.startswith("realign_node ")]) == 4


def test_restore_reports_final_gaze_neutral_mismatch_as_a_warning(tmp_path):
    cmds, baseline = _baseline(tmp_path); mel = _Mel()
    for alias in ("A", "B"):
        eye = f"{alias}:eyeStare"; both = f"{alias}:bothEyes"
        baseline["actors"][alias]["gaze_reference"] = {
            "eye_stare_node": eye,
            "eye_stare_translate": [0.0, 0.0, 0.0],
            "both_eyes_node": both,
            "both_eyes_translate": [0.0, 0.0],
        }
        cmds.values.update({
            f"{eye}.translateX": 0.0, f"{eye}.translateY": 0.0, f"{eye}.translateZ": 0.0,
            f"{both}.translateX": 0.0, f"{both}.translateY": 0.0,
        })
    cmds.values["A:eyeStare.translateX"] = 5.0
    original_set_attr = cmds.setAttr

    def leave_a_gaze_x_unchanged(plug, value, **kwargs):
        if plug == "A:eyeStare.translateX":
            return
        original_set_attr(plug, value, **kwargs)

    cmds.setAttr = leave_a_gaze_x_unchanged
    result = restore_dual_jali_base(
        baseline=baseline, character_mappings=_mappings(), cmds_module=cmds, mel_module=mel
    )

    assert result["restored"] == {"A": "AGNES", "B": "WILL"}
    assert result["warnings"] == ["A: JALI Base final gaze neutral validation failed."]


def test_named_restore_reasserts_gaze_neutral_after_realign(tmp_path, monkeypatch):
    import animation_apply_runner as runner

    class NamedCmds(_Cmds):
        def __init__(self, source_dir):
            super().__init__(source_dir)
            self.nodes = {"|ALICE:ROOT", "|BOB:ROOT", "|ALICE:ROOT|jSyncA", "|BOB:ROOT|jSyncB", "JALITEST_listenerMask_ALICE", "JALITEST_listenerMask_BOB", "JALITEST_gaze_ALICE", "JALITEST_gaze_BOB", "JALITEST_microSaccade_ALICE", "JALITEST_microSaccade_BOB"}
            for actor, z, x, y in (("ALICE", 9.0, 1.0, 2.0), ("BOB", 11.0, 3.0, 4.0)):
                self.values.update({f"{actor}:eyeStare_world.translateZ": z, f"{actor}:CNT_BOTH_EYES.translateX": x, f"{actor}:CNT_BOTH_EYES.translateY": y})
        def objExists(self, item):
            return item in self.nodes or item in self.values or item.rsplit(".", 1)[0] in {"ALICE:eyeStare_world", "BOB:eyeStare_world", "ALICE:CNT_BOTH_EYES", "BOB:CNT_BOTH_EYES"}

    (tmp_path / "SA.txt").write_text("A"); (tmp_path / "SB.txt").write_text("B")
    cmds = NamedCmds(tmp_path); mel = _Mel()
    mappings = {"ALICE": {"script_name": "ALICE", "maya_node": "|ALICE:ROOT"}, "BOB": {"script_name": "BOB", "maya_node": "|BOB:ROOT"}}
    monkeypatch.setattr(runner, "resolve_jsync_for_character", lambda rig, **_kwargs: "|ALICE:ROOT|jSyncA" if rig == "|ALICE:ROOT" else "|BOB:ROOT|jSyncB")
    # Re-key the fixture's original jSync/FACS data to the named rigs.
    for old, new, facs in (("|A:ROOT|jSyncA", "|ALICE:ROOT|jSyncA", "ALICE:FACSMaster"), ("|B:ROOT|jSyncB", "|BOB:ROOT|jSyncB", "BOB:FACSMaster")):
        for plug, value in list(cmds.values.items()):
            if plug.startswith(old): cmds.values[new + plug[len(old):]] = value
        cmds.values[f"{facs}.FACS_animationSource"] = 1
    baseline = capture_dual_jali_base(character_mappings=mappings, cmds_module=cmds)
    # Simulate realign changing the neutral; restore must overwrite it afterwards.
    original_eval = mel.eval
    def realign(command):
        value = original_eval(command)
        if command.startswith("realign_node"):
            actor = "ALICE" if "jSyncA" in command else "BOB"
            cmds.values[f"{actor}:eyeStare_world.translateX"] = 99.0
            cmds.values[f"{actor}:eyeStare_world.translateY"] = 99.0
        return value
    mel.eval = realign
    result = restore_dual_jali_base(baseline=baseline, character_mappings=mappings, cmds_module=cmds, mel_module=mel)
    assert set(result["restored"]) == {"ALICE", "BOB"}
    assert set(result["removed_layers"]) == {"JALITEST_listenerMask_ALICE", "JALITEST_listenerMask_BOB", "JALITEST_gaze_ALICE", "JALITEST_gaze_BOB", "JALITEST_microSaccade_ALICE", "JALITEST_microSaccade_BOB"}
    assert cmds.values["|ALICE:ROOT|jSyncA.transcript"] == "original A"
    assert cmds.values["|BOB:ROOT|jSyncB.transcript"] == "original B"
    assert cmds.values["ALICE:eyeStare_world.translateX"] == 0.0
    assert cmds.values["BOB:eyeStare_world.translateY"] == 0.0
    assert cmds.values["ALICE:eyeStare_world.translateZ"] == 9.0
    assert cmds.values["BOB:CNT_BOTH_EYES.translateX"] == 0.0
