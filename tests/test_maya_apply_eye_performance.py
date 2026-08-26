from __future__ import annotations

import json

import pytest

import expregaze_jali.maya_apply_eye_performance as eye


PRESETS = {
    "SINGLE_BLINK": {"closure": 7, "close_frames": 1, "hold_frames": 1, "open_frames": 1, "count": 1, "gap_frames": 0},
    "DOUBLE_BLINK": {"closure": 7, "close_frames": 1, "hold_frames": 1, "open_frames": 1, "count": 2, "gap_frames": 1},
}


class _Cmds:
    def __init__(self):
        self.calls = []

    def objExists(self, _plug): return True
    def keyframe(self, _node, **kwargs):
        if kwargs.get("timeChange"): return [10, 50, 90]
        if kwargs.get("valueChange"): return [0.0, 7.0, 0.0]
        if kwargs.get("eval"): return [0.0]
        return []
    def cutKey(self, *args, **kwargs): self.calls.append(("cutKey", args, kwargs))
    def setAttr(self, *args, **kwargs): self.calls.append(("setAttr", args, kwargs))
    def setKeyframe(self, *args, **kwargs): self.calls.append(("setKeyframe", args, kwargs))
    def keyTangent(self, *args, **kwargs): self.calls.append(("keyTangent", args, kwargs))


def _apply(monkeypatch, tmp_path, events, cmds):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "eye.json"
    path.write_text(json.dumps({"events": events}))
    monkeypatch.setattr(eye, "_cmds", lambda: cmds)
    monkeypatch.setattr(eye, "find_node_by_suffix", lambda name: name)
    eye.apply_eye_performance_events(
        eye_events_path=str(path), fps=24, eyelid_control_suffix="actor:LIDS", eyelid_attr="lid",
        blink_presets=PRESETS, preserve_existing_regulatory_blinks=True,
        apply_lid_states=False, apply_weighted_flat_tangents=False,
    )


def test_dual_preserve_mode_keeps_existing_jali_curve_without_semantic_blinks(monkeypatch, tmp_path):
    cmds = _Cmds()
    _apply(monkeypatch, tmp_path, [], cmds)
    assert cmds.calls == []


def test_dual_suppression_clears_only_its_regulatory_window(monkeypatch, tmp_path):
    cmds = _Cmds()
    _apply(monkeypatch, tmp_path, [{"type": "blink_suppression", "id": "P06", "value": "SUPPRESS", "resolved_time": {"start": 40 / 24, "end": 70 / 24}}], cmds)
    cut = [call for call in cmds.calls if call[0] == "cutKey"]
    assert cut == [("cutKey", ("actor:LIDS",), {"attribute": "lid", "time": (40, 70), "clear": True})]
    assert all(call[2].get("time") != (None, None) for call in cut)


def test_performative_blink_locally_overrides_jali_keys_and_wins_over_suppression(monkeypatch, tmp_path):
    cmds = _Cmds()
    _apply(monkeypatch, tmp_path, [
        {"type": "blink_suppression", "id": "P06", "value": "SUPPRESS", "resolved_time": {"start": 1, "end": 3}},
        {"type": "performative_blink", "id": "P05", "value": "DOUBLE_BLINK", "resolved_time": {"start": 2, "end": 3}},
    ], cmds)
    windows = [call[2]["time"] for call in cmds.calls if call[0] == "cutKey"]
    assert (24, 72) in windows
    assert (48, 55) in windows
    assert any(call[0] == "setKeyframe" and call[2]["time"] == 55.0 for call in cmds.calls)


def test_dual_mode_defers_lid_state_mutation(monkeypatch, tmp_path):
    cmds = _Cmds()
    _apply(monkeypatch, tmp_path, [{"type": "lid_state", "id": "P01", "value": -2, "resolved_time": {"start": 0, "end": 1}}], cmds)
    assert cmds.calls == []


def test_actor_b_blink_overlay_does_not_touch_actor_a_eyelid_curve(monkeypatch, tmp_path):
    cmds_a, cmds_b = _Cmds(), _Cmds()
    _apply(monkeypatch, tmp_path / "a", [], cmds_a)
    _apply(monkeypatch, tmp_path / "b", [{"type": "blink_suppression", "value": "SUPPRESS", "resolved_time": {"start": 1, "end": 2}}], cmds_b)
    assert cmds_a.calls == []
    assert [call[2]["time"] for call in cmds_b.calls if call[0] == "cutKey"] == [(24, 48)]


def test_blink_helpers_report_pattern_and_suppression_ranges():
    event = {"value": "DOUBLE_BLINK", "resolved_time": {"start": 2, "end": 3}}
    assert eye.performative_blink_frame_span(event, 24, PRESETS) == (48, 55)
    assert eye.blink_suppression_frame_intervals([{"value": "SUPPRESS", "resolved_time": {"start": 1, "end": 2}}], 24)[0][:2] == (24, 48)
