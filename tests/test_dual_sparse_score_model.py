from __future__ import annotations

from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model
from tools.maya.dual_sparse_score_model import DualSparseScoreModel, build_dialogue_projection


SCRIPT = "ALICE: We're very dangerous.\nBOB: No."
ANCHORS = build_conversation_anchor_model(SCRIPT, character_a="ALICE", character_b="BOB")
PLAN = {
    "schema_version": "dual_performance_plan_v2",
    "sequence_id": "x",
    "characters": ["ALICE", "BOB"],
    "initial_states": {
        "ALICE": {"affect": "Watchful-80", "gaze": "GAZE-BOB", "head": "HEAD-NONE"},
        "BOB": {"affect": "Neutral-60", "gaze": "GAZE-ALICE", "head": "HEAD-NONE"},
    },
    "initial_reasons": {"ALICE": "Begins guarded.", "BOB": "Begins composed."},
    "tracks": {
        "ALICE": [
            {"event_id": "E003", "anchor_id": "w0004", "changes": {"affect": "Watchful-100"}, "reason": "The denial increases suspicion."},
        ],
        "BOB": [
            {"event_id": "E002", "anchor_id": "w0003", "changes": {"affect": "Nervous-60", "gaze": "GAZE-DOWN"}, "reason": "The threat lands."},
        ],
    },
}


def test_projection_omits_prefixes_and_preserves_speaker_ranges():
    projection = build_dialogue_projection(ANCHORS)
    assert projection.display_text == "We're very dangerous.\nNo."
    assert "ALICE:" not in projection.display_text and "BOB:" not in projection.display_text
    assert [row.speaker for row in projection.speaker_ranges] == ["ALICE", "BOB"]
    assert projection.anchor_map["w0003"].text == "dangerous."


def test_two_actor_scores_render_role_aware_sparse_tags():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    assert model.score_texts["ALICE"].startswith("<Watchful-80><GAZE-BOB>We're")
    assert "No.<Watchful-100>" in model.score_texts["ALICE"]
    assert "dangerous.<Nervous-60><GAZE-DOWN>" in model.score_texts["BOB"]
    assert "<Watchful" not in model.score_texts["BOB"]
    assert all(model.validate_actor(actor, text).valid for actor, text in model.score_texts.items())


def test_score_rejects_dialogue_edits_and_wrong_side_or_whitespace_tags():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"].replace("dangerous", "safe")).valid
    assert not model.validate_actor("BOB", model.score_texts["BOB"].replace("dangerous.<Nervous-60>", "<Nervous-60>dangerous.")).valid
    assert model.validate_actor("ALICE", model.score_texts["ALICE"].replace("<GAZE-BOB>We're", "<GAZE-BOB>\n  We're")).valid
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"].replace("<GAZE-BOB>", "<GLANCE-NONE>", 1)).valid


def test_editing_tags_updates_only_sparse_track_and_reason_view():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    texts = dict(model.score_texts)
    texts["BOB"] = texts["BOB"].replace("<Nervous-60>", "<Happy-120>")
    applied = model.apply(texts)
    assert applied["tracks"]["BOB"][0]["changes"]["affect"] == "Happy-120"
    assert applied["tracks"]["ALICE"][0]["changes"] == PLAN["tracks"]["ALICE"][0]["changes"]
    assert applied["tracks"]["ALICE"][0]["reason_status"] == "llm_original"
    assert applied["tracks"]["BOB"][0]["source_event_id"] == "E002"
    assert applied["tracks"]["BOB"][0]["reason_status"] == "needs_confirmation"
    reason = model.rationale_view(3)
    assert "ALICE @ \"No.\"" in reason and "affect -> Watchful-100" in reason


def test_edited_reason_requires_explicit_confirmation_or_animator_replacement():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    texts = dict(model.score_texts)
    texts["BOB"] = texts["BOB"].replace("<Nervous-60>", "<Happy-120>")
    plan = model.apply(texts)
    assert plan["tracks"]["BOB"][0]["reason_status"] == "needs_confirmation"
    model.confirm_reason("BOB", "E002")
    assert model.plan["tracks"]["BOB"][0]["reason_status"] == "user_confirmed"
    # A subsequent score apply preserves the explicit confirmation.
    assert model.apply(texts)["tracks"]["BOB"][0]["reason_status"] == "user_confirmed"
    model.set_reason("BOB", "E002", "The new warmth defuses the threat.")
    event = model.plan["tracks"]["BOB"][0]
    assert event["reason_status"] == "user_edited"
    assert event["original_reason"] == "The threat lands."
    assert event["original_changes"] == {"affect": "Nervous-60", "gaze": "GAZE-DOWN"}
    assert "Original LLM reason" in model.rationale_view(4)


def test_initial_edit_has_separate_reason_provenance():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    texts = dict(model.score_texts)
    texts["ALICE"] = texts["ALICE"].replace("<Watchful-80>", "<Happy-80>", 1)
    plan = model.apply(texts)
    row = plan["initial_provenance"]["ALICE"]
    assert row["source_event_id"] == "INITIAL:ALICE"
    assert row["original_state"]["affect"] == "Watchful-80"
    assert row["reason_status"] == "needs_confirmation"
    model.confirm_reason("ALICE", "INITIAL:ALICE")
    assert model.plan["initial_provenance"]["ALICE"]["reason_status"] == "user_confirmed"


def test_score_requires_visible_initial_affect_and_initial_reason():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"].replace("<Watchful-80>", "", 1)).valid
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"].replace("<Watchful-80>", "<MASK-NONE>", 1)).valid
    model.plan["initial_reasons"]["ALICE"] = ""
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"]).valid


def test_real_listener_initial_state_and_token_adjacent_changes():
    script = (
        "ALICE: Evening, ma'am. We're in pursuit of someone very dangerous.\n"
        "ALICE: He might have come onto your property.\n"
        "ALICE: Have you seen anyone recently?\n"
        "BOB: No.\n"
        "BOB: Bert!"
    )
    anchors = build_conversation_anchor_model(script, character_a="ALICE", character_b="BOB")
    by_text = {}
    for anchor in anchors.anchors:
        by_text.setdefault(anchor.text, []).append(anchor.anchor_id)
    plan = {
        "schema_version": "dual_performance_plan_v2", "characters": ["ALICE", "BOB"],
        "initial_states": {
            "ALICE": {"affect": "Neutral-60", "gaze": "GAZE-BOB", "head": "HEAD-NONE"},
            "BOB": {"affect": "Watchful-85", "gaze": "GAZE-ALICE", "head": "HEAD-NONE"},
        },
        "initial_reasons": {"ALICE": "Begins neutral.", "BOB": "Begins watchful."},
        "tracks": {"ALICE": [], "BOB": [
            {"event_id": "E1", "anchor_id": by_text["dangerous."][0], "changes": {"gaze": "GAZE-DOWN"}, "reason": "The dangerous description raises concern."},
            {"event_id": "E2", "anchor_id": by_text["No."][0], "changes": {"head": "HEAD-DOWN-SUBTLE"}, "reason": "Contains the denial."},
            {"event_id": "E3", "anchor_id": by_text["Bert!"][0], "changes": {"gaze": "GAZE-RIGHT", "head": "HEAD-UP-MEDIUM"}, "reason": "Redirects attention."},
        ]},
    }
    model = DualSparseScoreModel(plan, anchors)
    score = model.score_texts["BOB"]
    assert score.startswith("<Watchful-85><GAZE-ALICE>Evening,")
    assert "dangerous.<GAZE-DOWN>" in score
    assert "<HEAD-DOWN-SUBTLE>No." in score
    assert "<GAZE-RIGHT><HEAD-UP-MEDIUM>Bert!" in score
    assert model.validate_actor("BOB", score).valid
    applied = model.apply({"ALICE": model.score_texts["ALICE"], "BOB": score})
    assert applied["initial_states"]["BOB"]["affect"] == "Watchful-85"
    assert applied["initial_states"]["BOB"]["gaze"] == "GAZE-ALICE"
    assert applied["tracks"]["BOB"][0]["anchor_id"] == by_text["dangerous."][0]
    assert model.validate_actor("BOB", score.replace("\n", "   \n\n  ")).valid
    assert not model.validate_actor("BOB", score.replace("dangerous.", "safe.")).valid
    assert not model.validate_actor("BOB", score.replace("Have you", "you Have")).valid
    assert not model.validate_actor("BOB", score.replace("Evening,", "ALICE: Evening,")).valid
    ambiguous = score.replace("<GAZE-RIGHT><HEAD-UP-MEDIUM>Bert!", " <GAZE-RIGHT><HEAD-UP-MEDIUM> Bert!")
    issues = [error.message for error in model.validate_actor("BOB", ambiguous).errors if "cluster placement" in error.message]
    assert len(issues) == 1
