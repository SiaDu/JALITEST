from __future__ import annotations

import re

from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model
from tools.maya.dual_sparse_score_model import (
    DialogueProjection,
    DisplayAnchor,
    DualSparseScoreModel,
    build_dialogue_projection,
    projection_offset_from_score_plain_offset,
    resolve_tag_offset_to_anchor,
)


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
    assert model.initial_score_texts["ALICE"] == "<Watchful-80><GAZE-BOB>"
    assert model.score_texts["ALICE"].startswith("We're")
    assert "<Watchful-100>No." in model.score_texts["ALICE"]
    assert "<Nervous-60><GAZE-DOWN>dangerous." in model.score_texts["BOB"]
    assert "<Watchful" not in model.score_texts["BOB"]
    assert model.initial_score_texts["BOB"] == "<Neutral-60><GAZE-ALICE>"
    assert all(model.validate_actor(actor, text).valid for actor, text in model.score_texts.items())


def _first_word_plan(changes: dict[str, str] | None = None) -> dict:
    return {
        "schema_version": "dual_performance_plan_v2", "characters": ["ALICE", "BOB"],
        "initial_states": {
            "ALICE": {"affect": "Watchful-80", "gaze": "GAZE-BOB", "head": "HEAD-NONE"},
            "BOB": {"affect": "Neutral-60", "gaze": "GAZE-ALICE", "head": "HEAD-NONE"},
        },
        "initial_reasons": {"ALICE": "Begins guarded.", "BOB": "Begins composed."},
        "tracks": {"ALICE": ([] if changes is None else [{"event_id": "E001", "anchor_id": "w0001", "changes": changes, "reason": "First-word change."}]), "BOB": []},
    }


def test_first_word_head_renders_in_dialogue_and_roundtrips():
    model = DualSparseScoreModel(_first_word_plan({"head": "HEAD-UP-SUBTLE"}), ANCHORS)
    score = model.score_texts["ALICE"]
    assert model.initial_score_texts["ALICE"] == "<Watchful-80><GAZE-BOB>"
    assert score.startswith("<HEAD-UP-SUBTLE>We're")
    assert model.validate_actor("ALICE", score).valid
    applied = model.apply(dict(model.score_texts))
    assert applied["initial_states"]["ALICE"]["gaze"] == "GAZE-BOB"
    assert applied["tracks"]["ALICE"] == [{**applied["tracks"]["ALICE"][0], "anchor_id": "w0001", "changes": {"head": "HEAD-UP-SUBTLE"}}]
    assert DualSparseScoreModel(applied, ANCHORS).score_texts["ALICE"] == score


def test_first_word_same_channel_affect_blink_and_glance_are_sparse_not_initial():
    for changes in (
        {"gaze": "GAZE-DOWN"}, {"affect": "Nervous-70"},
        {"blink": "SLOW_BLINK"}, {"gaze": "GLANCE-DOWN"},
    ):
        model = DualSparseScoreModel(_first_word_plan(changes), ANCHORS)
        validation = model.validate_actor("ALICE", model.score_texts["ALICE"])
        assert validation.valid, [error.message for error in validation.errors]
        applied = model.apply(dict(model.score_texts))
        assert applied["initial_states"]["ALICE"] == _first_word_plan()["initial_states"]["ALICE"]
        assert applied["tracks"]["ALICE"][0]["anchor_id"] == "w0001"
        assert applied["tracks"]["ALICE"][0]["changes"] == changes


def test_animator_can_add_first_word_event_for_speaking_or_listening_actor():
    model = DualSparseScoreModel(_first_word_plan(), ANCHORS)
    texts = dict(model.score_texts)
    texts["ALICE"] = texts["ALICE"].replace("We're", "<HEAD-UP-SUBTLE>We're")
    applied = model.apply(texts)
    assert applied["tracks"]["ALICE"][0]["anchor_id"] == "w0001"
    assert applied["tracks"]["ALICE"][0]["changes"] == {"head": "HEAD-UP-SUBTLE"}

    listener_text = model.score_texts["BOB"].replace("We're", "<HEAD-UP-SUBTLE>We're")
    listener = model.validate_actor("BOB", listener_text)
    assert listener.valid
    assert listener.events[-1] == {"actor": "BOB", "anchor_id": "w0001", "changes": {"head": "HEAD-UP-SUBTLE"}}


def test_dialogue_score_offsets_are_canonical_projection_offsets():
    score = DualSparseScoreModel(_first_word_plan(), ANCHORS).score_texts["ALICE"]
    assert projection_offset_from_score_plain_offset(score, 0) == 0


def test_dedicated_initial_score_rejects_blink_and_glance():
    model = DualSparseScoreModel(_first_word_plan(), ANCHORS)
    score = model.score_texts["ALICE"]
    initial = model.initial_score_texts["ALICE"]
    assert not model.validate_actor("ALICE", score, initial + "<SLOW_BLINK>").valid
    assert not model.validate_actor("ALICE", score, initial.replace("<GAZE-BOB>", "<GLANCE-DOWN>")).valid


def test_score_rejects_dialogue_edits_but_accepts_free_tag_placement():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"].replace("dangerous", "safe")).valid
    moved = model.score_texts["BOB"].replace("<Nervous-60><GAZE-DOWN>dangerous.", "dangerous.<Nervous-60><GAZE-DOWN>")
    assert model.validate_actor("BOB", moved).valid
    assert model.validate_actor("ALICE", model.score_texts["ALICE"].replace("We're", "\n  We're")).valid
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"], model.initial_score_texts["ALICE"].replace("<GAZE-BOB>", "<GLANCE-NONE>")).valid


def test_editing_tags_updates_only_sparse_track_and_reason_view():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    texts = dict(model.score_texts)
    texts["BOB"] = texts["BOB"].replace("<Nervous-60>", "<Happy-120>")
    applied = model.apply(texts)
    assert applied["tracks"]["BOB"][0]["changes"]["affect"] == "Happy-120"
    assert applied["tracks"]["ALICE"][0]["changes"] == PLAN["tracks"]["ALICE"][0]["changes"]
    assert applied["tracks"]["ALICE"][0]["reason_status"] == "llm_original"
    assert applied["tracks"]["BOB"][0]["source_event_id"] == "E002"
    assert applied["tracks"]["BOB"][0]["reason_status"] == "stale_after_user_edit"
    assert applied["tracks"]["BOB"][0]["reason"] == "The threat lands."
    assert applied["tracks"]["BOB"][0]["changes"] == {"affect": "Happy-120", "gaze": "GAZE-DOWN"}
    reason = model.rationale_view(3)
    assert "ALICE @ \"No.\"" in reason and "affect -> Watchful-100" in reason


def test_canonical_before_token_syntax_round_trips_listener_anchor_without_changing_plan_semantics():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    assert "<Nervous-60><GAZE-DOWN>dangerous." in model.score_texts["BOB"]
    applied = model.apply(dict(model.score_texts))
    listener = applied["tracks"]["BOB"][0]
    assert listener["actor"] == "BOB"
    assert listener["anchor_id"] == "w0003"
    assert listener["changes"] == {"affect": "Nervous-60", "gaze": "GAZE-DOWN"}


def test_deleting_only_original_event_is_preserved_as_a_semantic_edit_baseline():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    texts = dict(model.score_texts)
    texts["BOB"] = texts["BOB"].replace("<Nervous-60><GAZE-DOWN>", "")
    applied = model.apply(texts)
    assert applied["tracks"]["BOB"] == []
    assert applied["provenance"]["original_authored_content"]["tracks"]["BOB"][0]["event_id"] == "E002"


def test_new_score_event_uses_user_edited_reason_without_an_original_rationale():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    texts = dict(model.score_texts)
    texts["BOB"] = texts["BOB"].replace("No.", "<HEAD-UP-SUBTLE>No.")
    applied = model.apply(texts)
    added = next(event for event in applied["tracks"]["BOB"] if event["anchor_id"] == "w0004")
    assert added["edited_by_user"] is True
    assert added["reason"] is None
    assert added["original_reason"] is None
    assert added["reason_status"] == "user_added_no_reason"

    reapplied = model.apply(dict(model.score_texts))
    repeated = next(event for event in reapplied["tracks"]["BOB"] if event["anchor_id"] == "w0004")
    assert repeated["reason"] is repeated["original_reason"] is None
    assert repeated["edited_by_user"] is True
    assert repeated["reason_status"] == "user_added_no_reason"

    edited = dict(model.score_texts)
    edited["BOB"] = edited["BOB"].replace("<HEAD-UP-SUBTLE>", "<HEAD-DOWN-SUBTLE>")
    edited_plan = model.apply(edited)
    changed = next(event for event in edited_plan["tracks"]["BOB"] if event["anchor_id"] == "w0004")
    assert changed["reason"] is changed["original_reason"] is None
    assert changed["edited_by_user"] is True
    assert changed["reason_status"] == "user_added_no_reason"


def test_edited_semantics_use_non_blocking_user_edited_provenance():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    texts = dict(model.score_texts)
    texts["BOB"] = texts["BOB"].replace("<Nervous-60>", "<Happy-120>")
    plan = model.apply(texts)
    event = plan["tracks"]["BOB"][0]
    assert event["reason_status"] == "stale_after_user_edit"
    assert event["reason"] == "The threat lands."
    assert event["edited_by_user"] is True
    assert event["original_reason"] == "The threat lands."
    assert event["original_changes"] == {"affect": "Nervous-60", "gaze": "GAZE-DOWN"}
    rationale = model.rationale_view(4)
    assert "Rationale:\nThe threat lands." in rationale
    assert "Original rationale — semantic tag has been edited." in rationale


def test_reverting_semantic_and_initial_tags_restores_llm_provenance():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    edited = dict(model.score_texts)
    edited["BOB"] = edited["BOB"].replace("<Nervous-60>", "<Happy-120>")
    model.apply(edited)
    reverted = dict(model.score_texts)
    reverted["BOB"] = reverted["BOB"].replace("<Happy-120>", "<Nervous-60>")
    plan = model.apply(reverted)
    event = plan["tracks"]["BOB"][0]
    assert event["edited_by_user"] is False
    assert event["reason_status"] == "llm_original"
    assert event["reason"] == event["original_reason"] == "The threat lands."

    edited = dict(model.score_texts)
    edited["ALICE"] = edited["ALICE"].replace("<Watchful-80>", "<Happy-80>", 1)
    model.apply(edited)
    reverted = dict(model.score_texts)
    reverted["ALICE"] = reverted["ALICE"].replace("<Happy-80>", "<Watchful-80>", 1)
    plan = model.apply(reverted)
    initial = plan["initial_provenance"]["ALICE"]
    assert initial["edited_by_user"] is False
    assert initial["reason_status"] == "llm_original"
    assert initial["reason"] == initial["original_reason"] == "Begins guarded."


def test_user_added_event_rationale_identifies_absent_llm_reason():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    texts = dict(model.score_texts)
    texts["BOB"] = texts["BOB"].replace("No.", "<HEAD-UP-SUBTLE>No.")
    model.apply(texts)
    assert "No original acting interpretation" in model.rationale_view(5)
    assert "user-added semantic change." in model.rationale_view(5)


def test_initial_edit_has_separate_reason_provenance():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    texts = {actor: {"dialogue": model.score_texts[actor], "initial": model.initial_score_texts[actor]} for actor in model.characters}
    texts["ALICE"]["initial"] = texts["ALICE"]["initial"].replace("<Watchful-80>", "<Happy-80>", 1)
    plan = model.apply(texts)
    row = plan["initial_provenance"]["ALICE"]
    assert row["source_event_id"] == "INITIAL:ALICE"
    assert row["original_state"]["affect"] == "Watchful-80"
    assert row["reason_status"] == "stale_after_user_edit"
    assert row["reason"] == "Begins guarded."


def test_score_requires_visible_initial_affect_and_initial_reason():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"], model.initial_score_texts["ALICE"].replace("<Watchful-80>", "", 1)).valid
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"], model.initial_score_texts["ALICE"].replace("<Watchful-80>", "<MASK-NONE>", 1)).valid
    model.plan["initial_reasons"]["ALICE"] = ""
    assert model.validate_actor("ALICE", model.score_texts["ALICE"]).valid


def test_score_rejects_reserved_target_tags_case_insensitively():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    assert not model.validate_actor("ALICE", model.score_texts["ALICE"], model.initial_score_texts["ALICE"].replace("GAZE-BOB", "GAZE-target")).valid
    assert not model.validate_actor("BOB", model.score_texts["BOB"].replace("<Nervous-60>", "<Nervous-60><GLANCE-TARGET>")).valid


def test_score_validation_rejects_invalid_authored_blink_hold_sequences_early():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    score = model.score_texts["BOB"]
    assert not model.validate_actor("BOB", score.replace("No.", "<EYE_OPEN>No.")).valid
    held = score.replace("<Nervous-60><GAZE-DOWN>", "<Nervous-60><GAZE-DOWN><EYE_CLOSE_HOLD>")
    assert not model.validate_actor("BOB", held.replace("No.", "<SLOW_BLINK>No.")).valid
    valid = held.replace("No.", "<EYE_OPEN>No.")
    assert model.validate_actor("BOB", valid).valid


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
    assert model.initial_score_texts["BOB"] == "<Watchful-85><GAZE-ALICE>"
    assert score.startswith("Evening,")
    assert "<GAZE-DOWN>dangerous." in score
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
    free_placement = score.replace("<GAZE-RIGHT><HEAD-UP-MEDIUM>Bert!", " <GAZE-RIGHT><HEAD-UP-MEDIUM> Bert!")
    assert model.validate_actor("BOB", free_placement).valid


FREE_SCRIPT = "MARTY: Whoa.\nDION: Yep.\nMARTY: How'd you do the orange?"
FREE_ANCHORS = build_conversation_anchor_model(FREE_SCRIPT, character_a="MARTY", character_b="DION")


def _free_placement_model() -> DualSparseScoreModel:
    return DualSparseScoreModel({
        "schema_version": "dual_performance_plan_v2",
        "characters": ["MARTY", "DION"],
        "initial_states": {
            "MARTY": {"affect": "Neutral-60", "gaze": "GAZE-DION", "head": "HEAD-NONE"},
            "DION": {"affect": "Neutral-60", "gaze": "GAZE-MARTY", "head": "HEAD-NONE"},
        },
        "initial_reasons": {"MARTY": "Listening.", "DION": "Listening."},
        "tracks": {"MARTY": [], "DION": []},
    }, FREE_ANCHORS)


def _event_for(validation, anchor_id: str) -> dict:
    return next(event for event in validation.events if not event.get("initial") and event["anchor_id"] == anchor_id)


def test_resolve_tag_offset_to_anchor_uses_inclusive_end_and_forward_tie_break():
    projection = DialogueProjection(
        "Whoa.  Yep.",
        (
            DisplayAnchor("w1", "Whoa.", "MARTY", 0, 5),
            DisplayAnchor("w2", "Yep.", "DION", 7, 11),
        ),
        (),
    )
    assert resolve_tag_offset_to_anchor(projection, -2).anchor_id == "w1"
    assert resolve_tag_offset_to_anchor(projection, 0).anchor_id == "w1"
    assert resolve_tag_offset_to_anchor(projection, 5).anchor_id == "w1"
    assert resolve_tag_offset_to_anchor(projection, 6).anchor_id == "w2"
    assert resolve_tag_offset_to_anchor(projection, 99).anchor_id == "w2"


def test_free_tag_placement_snaps_marty_panel_tags_without_editing_dialogue():
    model = _free_placement_model()
    score = model.score_texts["MARTY"]
    assert model.projection.display_text.replace("\n", " ") == "Whoa. Yep. How'd you do the orange?"
    anchors = {anchor.text: anchor.anchor_id for anchor in model.projection.anchors}
    variants = {
        "prefix": (score.replace("Whoa.", "<Happy-80>Whoa.", 1), anchors["Whoa."]),
        "split": (score.replace("Whoa.", "Wh<Happy-80>oa.", 1), anchors["Whoa."]),
        "postfix": (score.replace("Whoa.", "Whoa.<Happy-80>", 1), anchors["Whoa."]),
        "between": (score.replace("Whoa.\nYep.", "Whoa. <Happy-80> Yep.", 1), anchors["Yep."]),
        "newline": (score.replace("Whoa.\nYep.", "Whoa.\n<Happy-80>\nYep.", 1), anchors["Yep."]),
        "end": (score + "<Happy-80>", anchors["orange?"]),
        "start": ("<Happy-80>" + score, anchors["Whoa."]),
    }
    for name, (edited, expected_anchor) in variants.items():
        validation = model.validate_actor("MARTY", edited)
        assert validation.valid, (name, [issue.message for issue in validation.errors])
        assert _event_for(validation, expected_anchor)["changes"] == {"affect": "Happy-80"}
        assert "".join(re.sub(r"<[^<>]+>", "", edited).split()) == "".join(model.projection.display_text.split())


def test_split_token_tag_round_trips_to_canonical_before_anchor_syntax():
    model = _free_placement_model()
    texts = dict(model.score_texts)
    texts["MARTY"] = texts["MARTY"].replace("Whoa.", "Wh<Happy-80>oa.", 1)
    applied = model.apply(texts)
    change = applied["tracks"]["MARTY"][0]
    assert change["actor"] == "MARTY"
    assert change["anchor_id"] == model.projection.anchors[0].anchor_id
    assert change["changes"] == {"affect": "Happy-80"}
    refreshed = DualSparseScoreModel(applied, FREE_ANCHORS)
    assert "<Happy-80>Whoa." in refreshed.score_texts["MARTY"]
    assert "Wh<Happy-80>oa." not in refreshed.score_texts["MARTY"]


def test_listener_panel_can_tag_dions_word_from_prefix_split_or_postfix():
    model = _free_placement_model()
    dion_yep = next(anchor for anchor in model.projection.anchors if anchor.speaker == "DION" and anchor.text == "Yep.")
    variants = (
        model.score_texts["MARTY"].replace("Yep.", "<Surprised-70>Yep.", 1),
        model.score_texts["MARTY"].replace("Yep.", "Ye<Surprised-70>p.", 1),
        model.score_texts["MARTY"].replace("Yep.", "Yep.<Surprised-70>", 1),
    )
    for edited in variants:
        validation = model.validate_actor("MARTY", edited)
        assert validation.valid, [issue.message for issue in validation.errors]
        event = _event_for(validation, dion_yep.anchor_id)
        assert event == {"actor": "MARTY", "anchor_id": dion_yep.anchor_id, "changes": {"affect": "Surprised-70"}}
        assert dion_yep.speaker == "DION" and event["actor"] != dion_yep.speaker


def test_different_channels_snapping_to_one_anchor_merge_but_same_channel_collides():
    model = _free_placement_model()
    merged = model.score_texts["MARTY"].replace("Whoa.", "<Happy-80>Whoa.<GAZE-DION>", 1)
    validation = model.validate_actor("MARTY", merged)
    assert validation.valid
    assert _event_for(validation, model.projection.anchors[0].anchor_id)["changes"] == {"affect": "Happy-80", "gaze": "GAZE-DION"}

    collision = model.score_texts["MARTY"].replace("Whoa.", "<Happy-80>Whoa.<Neutral-60>", 1)
    assert not model.validate_actor("MARTY", collision).valid


def test_generated_plan_preserves_actor_anchor_and_changes_after_render_validate_apply():
    model = DualSparseScoreModel(PLAN, ANCHORS)
    applied = model.apply(dict(model.score_texts))

    def semantic_rows(plan):
        return [
            (actor, event["anchor_id"], event["changes"])
            for actor in plan["characters"]
            for event in plan["tracks"][actor]
        ]

    assert semantic_rows(applied) == semantic_rows(PLAN)
