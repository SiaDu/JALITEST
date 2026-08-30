from __future__ import annotations

from expregaze_jali.compile_dual_semantic_beats import compile_dual_semantic_beats, render_compiled_dual_performance_proposal
from expregaze_jali.dual_performance_plan_v2 import build_dual_performance_plan_v2
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model


MODEL = build_conversation_anchor_model("AGNES: One.\nWILL: Two.\nAGNES: Three.", character_a="AGNES", character_b="WILL")


def test_focus_and_eye_action_compiler_derives_gaze_state_and_calibration_metadata():
    ir = {
        "initial": {
            "AGNES": {"affect": "Watchful-80", "focus": "WILL", "acting": "She watches Will.", "head": "HEAD-NONE"},
            "WILL": {"affect": "Neutral-60", "focus": "AGNES", "acting": "He watches Agnes.", "head": "HEAD-NONE"},
        },
        "beats": [
            {"event_id": "E1", "actor": "AGNES", "anchor_id": "w0001", "acting": "Checks upward.", "eye_action": {"action": "brief_check", "target": "UP_RIGHT"}},
            {"event_id": "E2", "actor": "AGNES", "anchor_id": "w0002", "acting": "Fear rises.", "affect": "Nervous-80"},
            {"event_id": "E3", "actor": "AGNES", "anchor_id": "w0003", "acting": "Inspects the package.", "focus": "PACKAGING"},
            {"event_id": "E4", "actor": "WILL", "anchor_id": "w0002", "acting": "Checks Rachel.", "eye_action": {"action": "brief_check", "target": "RACHEL"}},
            {"event_id": "E5", "actor": "WILL", "anchor_id": "w0003", "acting": "Still watches Agnes.", "focus": "AGNES"},
        ], "diagnostics": {"errors": [], "warnings": []},
    }
    proposal = compile_dual_semantic_beats(ir, anchor_model=MODEL)
    agnes = [row for row in proposal["events"] if row["actor"] == "AGNES"]
    assert proposal["initial_states"]["AGNES"]["gaze"] == "GAZE-WILL"
    assert agnes[0]["changes"]["gaze"] == "GLANCE-UP_RIGHT"
    assert agnes[1]["changes"] == {"affect": "Nervous-80"}
    assert agnes[2]["changes"]["gaze"] == "GAZE-PACKAGING"
    assert proposal["gaze_target_candidates"] == ["RACHEL", "PACKAGING"]
    assert all(row["event_id"] != "E5" for row in proposal["events"])
    assert "E5: dropped after no semantic changes remained" in proposal["diagnostics"]["warnings"]
    assert build_dual_performance_plan_v2(proposal, anchor_model=MODEL, sequence_id="semantic")["schema_version"] == "dual_performance_plan_v2"
    rendered = render_compiled_dual_performance_proposal(proposal, characters=("AGNES", "WILL"))
    assert "[GAZE_TARGETS]\nRACHEL\nPACKAGING" in rendered and "gaze: GLANCE-UP_RIGHT" in rendered


def test_eye_action_preserves_focus_and_derives_non_directional_candidates():
    ir = {
        "initial": {
            "AGNES": {"affect": "Watchful-80", "focus": "WILL", "acting": "She watches Will."},
            "WILL": {"affect": "Neutral-60", "focus": "AGNES", "acting": "He watches Agnes."},
        },
        "beats": [
            {"event_id": "E1", "actor": "WILL", "anchor_id": "w0001", "acting": "He looks down briefly.", "eye_action": {"action": "brief_check", "target": "DOWN"}},
            {"event_id": "E2", "actor": "WILL", "anchor_id": "w0002", "acting": "He checks the hawk.", "eye_action": {"action": "brief_check", "target": "HAWK"}},
            {"event_id": "E3", "actor": "WILL", "anchor_id": "w0003", "acting": "He remains focused on Agnes.", "focus": "AGNES"},
        ], "diagnostics": {"errors": [], "warnings": []},
    }
    proposal = compile_dual_semantic_beats(ir, anchor_model=MODEL)
    will = [event for event in proposal["events"] if event["actor"] == "WILL"]
    assert [event["changes"] for event in will] == [{"gaze": "GLANCE-DOWN"}, {"gaze": "GLANCE-HAWK"}]
    assert proposal["gaze_target_candidates"] == ["HAWK"]
    assert "E3: dropped after no semantic changes remained" in proposal["diagnostics"]["warnings"]
