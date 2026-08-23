from __future__ import annotations

import copy
from pathlib import Path
import sys


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from performance_score_model import (  # noqa: E402
    PerformanceScoreModel,
    format_dual_score,
    format_rationale_view,
    format_single_score,
    parse_score,
)


def _span(tag: str, start: int, end: int, value: str, **extra: object) -> dict:
    return {"source_tag": tag, "char_start": start, "char_end": end, "value": value, **extra}


def _plan(*, character: str = "PROFESSOR", second_state: str = "Smug") -> dict:
    return {
        "schema_version": "performance_plan_v0",
        "sequence_id": "scene",
        "target_character": character,
        "unknown_root": {"must": "survive"},
        "events": [
            {
                "event_id": "E01",
                "source_intent_tag": "i01",
                "span": {"text": "That's right. Here.", "char_start": 0, "char_end": 19},
                "intent": "WELCOME",
                "affect": {
                    "visible": [
                        _span("m01", 0, 13, "Friendly-66", state="Friendly", intensity=0.66),
                        _span("m02", 13, 19, f"{second_state}-72", state=second_state, intensity=0.72),
                    ],
                    "hidden": [],
                },
                "gaze": [
                    _span("g01", 0, 13, "GAZE-CHARACTER_DOROTHY", mode="GAZE", target="CHARACTER_DOROTHY"),
                    _span("g02", 13, 19, "GLANCE-DOWN", mode="GLANCE", target="DOWN"),
                ],
                "head": [_span("hd01", 0, 19, "MEDIUM", involvement=0.5)],
                "lid_state": [_span("l01", 0, 19, "-1", lid_state=-1)],
                "blink": {
                    "performative": [_span("pb01", 13, 19, "SLOW_BLINK")],
                    "suppression": [],
                },
                "rationale": {
                    "intent": {"source_tag": "i01", "reason": "Welcomes Dorothy."},
                    "affect": {
                        "visible": [
                            {"source_tag": "m01", "reason": "Starts warmly."},
                            {"source_tag": "m02", "reason": "Lets confidence show."},
                        ],
                        "hidden": [],
                    },
                    "gaze": [
                        {"source_tag": "g01", "reason": "Connects with Dorothy."},
                        {"source_tag": "g02", "reason": "Checks the placement."},
                    ],
                    "head": [{"source_tag": "hd01", "reason": "Stays engaged."}],
                    "lid_state": [{"source_tag": "l01", "reason": "Alert but controlled."}],
                    "blink": {
                        "performative": [{"source_tag": "pb01", "reason": "Marks the beat."}],
                        "suppression": [],
                    },
                },
                "evidence": {"transcript": "That's right. Here."},
                "locks": {"intent": False, "affect": False, "gaze": False, "head": False, "blink": False},
                "unknown_event": [1, 2, 3],
            }
        ],
        "diagnostics": {"errors": [], "warnings": []},
    }


def test_single_format_is_numbered_human_facing_and_expands_resolved_state():
    score = format_single_score(_plan())
    assert score.startswith("1. <l-1><Friendly-66><GAZE-DOROTHY>")
    assert "2. <l-1><Smug-72><GLANCE-DOWN><SLOW_BLINK>" in score
    assert "That's right." in score
    assert "Here." in score
    assert "source_tag" not in score
    assert "char_start" not in score
    assert "m01" not in score
    assert "</" not in score


def test_resolved_state_inheritance_is_repeated_after_original_span_end():
    plan = _plan()
    plan["events"][0]["lid_state"][0]["char_end"] = 13
    score = format_single_score(plan)
    assert score.count("<l-1>") == 2


def test_score_round_trip_applies_affect_gaze_lid_and_blink_edits():
    original = _plan()
    protected = copy.deepcopy({
        "root": original["unknown_root"],
        "event": original["events"][0]["unknown_event"],
        "event_span": original["events"][0]["span"],
        "source_tags": [span["source_tag"] for span in original["events"][0]["affect"]["visible"]],
    })
    model = PerformanceScoreModel(original)
    edited = model.score_text.replace(
        "<l-1><Smug-72><GLANCE-DOWN><SLOW_BLINK>",
        "<l2><Thinking-76><GAZE-CRYSTAL><EYE_CLOSE_HOLD>",
    )
    result = model.apply(edited)
    event = result["events"][0]
    assert event["affect"]["visible"][1]["state"] == "Thinking"
    assert event["affect"]["visible"][1]["intensity"] == 0.76
    assert event["gaze"][1]["mode"] == "GAZE"
    assert event["gaze"][1]["target"] == "CRYSTAL"
    assert [span["lid_state"] for span in event["lid_state"]] == [-1, 2]
    assert event["blink"]["performative"][0]["value"] == "EYE_CLOSE_HOLD"
    assert result["unknown_root"] == protected["root"]
    assert event["unknown_event"] == protected["event"]
    assert event["span"] == protected["event_span"]
    preserved = result["authoring"]["original_semantic_proposal"]["events"][0]
    assert [span["source_tag"] for span in preserved["affect"]["visible"]] == protected["source_tags"]
    assert event["affect"]["visible"][1]["original_source_tag"] == "m02"
    reloaded = PerformanceScoreModel(result)
    assert "2. <l2><Thinking-76><GAZE-CRYSTAL><EYE_CLOSE_HOLD>" in reloaded.score_text


def test_invalid_unknown_tag_has_phrase_specific_error_and_does_not_mutate_plan():
    model = PerformanceScoreModel(_plan())
    before = copy.deepcopy(model.plan)
    invalid = model.score_text.replace("<GLANCE-DOWN>", "<GASE-LISTENER>")
    result = model.validate(invalid)
    assert not result.valid
    assert "Phrase 2: Unknown behavior <GASE-LISTENER>" in [str(error) for error in result.errors]
    try:
        model.apply(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid score must not apply")
    assert model.plan == before


def test_parser_rejects_bad_numbering_and_out_of_range_affect():
    result = parse_score("2. <Friendly-101><GAZE-LISTENER>\nA line.")
    messages = [str(error) for error in result.errors]
    assert "Phrase 2: Unknown behavior <Friendly-101>" in messages
    assert "Phrase numbers must be unique, contiguous, and ordered from 1." in messages


def test_phrase_reason_lookup_returns_all_event_rationales():
    model = PerformanceScoreModel(_plan())
    reasons = model.rationale_for_phrase(2)
    assert {item.category for item in reasons} == {
        "Intent", "Visible Affect", "Gaze", "Head", "Lid", "Performative Blink"
    }
    assert len(reasons) == 6


def test_manual_edit_tracking_and_original_rationale_preservation():
    plan = _plan()
    original_rationale = copy.deepcopy(plan["events"][0]["rationale"])
    model = PerformanceScoreModel(plan)
    model.apply(model.score_text.replace("<Smug-72>", "<Thinking-76>"))
    assert model.is_manually_edited(2)
    assert model.plan["events"][0]["rationale"] == original_rationale
    view = format_rationale_view(model, 2)
    assert "Phrase manually edited. AI rationale corresponds to the original proposal." in view
    assert "Lets confidence show." in view


def test_dual_character_format_and_parser_use_shared_area_grammar():
    score = format_dual_score(_plan(character="PROFESSOR"), _plan(character="DOROTHY", second_state="Curious"), speakers=["A", "B"])
    assert "1. A:<l-1><Friendly-66><GAZE-DOROTHY> | B:<l-1><Friendly-66><GAZE-DOROTHY>" in score
    assert "   A: That's right." in score
    assert "2. A:<l-1><Smug-72><GLANCE-DOWN><SLOW_BLINK> | B:<l-1><Curious-72><GLANCE-DOWN><SLOW_BLINK>" in score
    assert "   B: Here." in score
    parsed = parse_score(score, mode="dual", known_targets={"DOROTHY", "DOWN"})
    assert parsed.valid
    assert [phrase.speaker for phrase in parsed.phrases] == ["A", "B"]
    assert parsed.phrases[1].states["B"].affect == ("Curious", 72)


def test_canonical_plan_and_offsets_remain_present_after_noop_round_trip():
    model = PerformanceScoreModel(_plan())
    result = model.apply(model.score_text)
    assert result["schema_version"] == "performance_plan_v0"
    assert result["events"][0]["span"]["char_start"] == 0
    assert result["events"][0]["span"]["char_end"] == 19
    assert result["events"][0]["gaze"][0]["char_start"] == 0
    assert result["events"][0]["gaze"][0]["char_end"] == 13
    assert result["unknown_root"] == {"must": "survive"}
    assert result["authoring"]["manually_edited_phrases"] == []
