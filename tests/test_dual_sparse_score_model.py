from __future__ import annotations

from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model
from tools.maya.dual_sparse_score_model import DualSparseScoreModel, build_dialogue_projection


SCRIPT = "ALICE: We're very dangerous.\nBOB: No."
ANCHORS = build_conversation_anchor_model(SCRIPT, character_a="ALICE", character_b="BOB")
PLAN = {
    "schema_version": "dual_performance_plan_v2",
    "sequence_id": "x",
    "characters": ["ALICE", "BOB"],
    "tracks": {
        "ALICE": [
            {"event_id": "E001", "anchor_id": "w0001", "changes": {"affect": "Watchful-80"}, "reason": "Begins guarded."},
            {"event_id": "E003", "anchor_id": "w0004", "changes": {"affect": "Watchful-100"}, "reason": "The denial increases suspicion."},
        ],
        "BOB": [
            {"event_id": "E002", "anchor_id": "w0003", "changes": {"affect": "Nervous-60", "gaze": "AVERT-ALICE"}, "reason": "The threat lands."},
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
    assert model.score_texts["ALICE"].startswith("<Watchful-80>We're")
    assert "No.<Watchful-100>" in model.score_texts["ALICE"]
    assert "dangerous.<Nervous-60><AVERT-ALICE>" in model.score_texts["BOB"]
    assert "<Watchful" not in model.score_texts["BOB"]
    assert all(model.validate_actor(actor, text).valid for actor, text in model.score_texts.items())


def test_score_rejects_dialogue_edits_and_wrong_side_or_whitespace_tags():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"].replace("dangerous", "safe")).valid
    assert not model.validate_actor("BOB", model.score_texts["BOB"].replace("dangerous.<Nervous-60>", "<Nervous-60>dangerous.")).valid
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"].replace("<Watchful-80>We're", "<Watchful-80> We're")).valid


def test_editing_tags_updates_only_sparse_track_and_reason_view():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    texts = dict(model.score_texts)
    texts["BOB"] = texts["BOB"].replace("<Nervous-60>", "<Happy-120>")
    applied = model.apply(texts)
    assert applied["tracks"]["BOB"][0]["changes"]["affect"] == "Happy-120"
    assert applied["tracks"]["ALICE"] == PLAN["tracks"]["ALICE"]
    reason = model.rationale_view(1)
    assert "ALICE @ \"We're\"" in reason and "affect -> Watchful-80" in reason
