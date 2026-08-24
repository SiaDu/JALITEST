from __future__ import annotations

from expregaze_jali.performance_plan_from_proposal import build_performance_plan_from_proposal
from expregaze_jali.performance_plan_schema import assert_no_timing_fields
from expregaze_jali.performance_proposal_parser import SemanticVocabulary, parse_performance_proposal
from expregaze_jali.transcript_anchor_model import build_transcript_anchor_model


VOCAB = SemanticVocabulary(
    affect_states={"nervous": "Nervous", "thinking": "Thinking"},
    heart_states={"angry": "Angry"},
)


PROPOSAL = """[ANALYZE]
Will hesitates, then constructs an explanation.

[PERFORMANCE]
S01
span: w0001-w0003
intent: hesitate and buy time
affect: nervous-55
heart: Angry-20
gaze: avert-down
head: low
lid: -1
blink: NONE
blink_suppression: SUPPRESS

S02
span: w0004-w0009
intent: construct plausible explanation
affect: Thinking-60
heart: NONE
gaze: GAZE-B
head: NONE
lid: NONE
blink: SLOW_BLINK
blink_suppression: NONE

[REASONS]
S01.intent: Hesitation buys time.
S01.affect: Nervousness is visible.
S01.heart: Anger stays hidden.
S01.gaze: Looking down avoids disclosure.
S01.head: The motion is contained.
S01.lid: Alert eyes preserve awareness.
S01.blink_suppression: Stillness sustains tension.
S02.intent: He constructs an explanation.
S02.affect: Thinking shows cognitive search.
S02.gaze: He returns attention to Agnes.
S02.head: A still head contains the thought.
S02.blink: Retrieval motivates a slow blink.
"""


def test_direct_plan_build_has_exact_spans_generated_tags_reasons_and_no_timing():
    script = "WILL: Yes, I, uh... I suppose the air was fresh...\nAGNES: Really?"
    anchors = build_transcript_anchor_model(script, target_character="WILL")
    proposal = parse_performance_proposal(PROPOSAL, vocabulary=VOCAB)
    plan = build_performance_plan_from_proposal(
        proposal, anchor_model=anchors, sequence_id="run_test", proposal_path="proposal.txt"
    )

    assert plan["schema_version"] == "performance_plan_v0"
    assert plan["acting_interpretation"] == "Will hesitates, then constructs an explanation."
    assert [event["span"]["text"] for event in plan["events"]] == [
        "Yes, I, uh... ", "I suppose the air was fresh..."
    ]
    assert plan["events"][0]["source_intent_tag"] == "i01"
    assert plan["events"][1]["source_intent_tag"] == "i02"
    assert plan["events"][0]["affect"]["visible"][0]["source_tag"] == "m01"
    assert plan["events"][0]["affect"]["hidden"][0]["source_tag"] == "h01"
    assert plan["events"][0]["head"][0]["source_tag"] == "hd01"
    assert plan["events"][0]["lid_state"][0]["source_tag"] == "l01"
    assert plan["events"][0]["blink"]["suppression"][0]["source_tag"] == "bs01"
    assert plan["events"][1]["blink"]["performative"][0]["source_tag"] == "pb01"
    assert plan["events"][1]["gaze"][0]["value"] == "GAZE-CHARACTER_AGNES"
    assert plan["events"][1]["head"][0]["value"] == "NONE"
    assert plan["events"][1]["head"][0]["involvement"] == 0.0
    assert plan["events"][0]["rationale"]["affect"]["visible"][0] == {
        "source_tag": "m01", "reason": "Nervousness is visible."
    }
    assert plan["source_annotation"] is None
    assert plan["source_proposal"] == "proposal.txt"
    all_tags = []
    for event in plan["events"]:
        all_tags.append(event["source_intent_tag"])
        for rows in (
            event["affect"]["visible"], event["affect"]["hidden"], event["gaze"],
            event["head"], event["lid_state"], event["blink"]["performative"],
            event["blink"]["suppression"],
        ):
            all_tags.extend(row["source_tag"] for row in rows)
    assert len(all_tags) == len(set(all_tags))
    assert_no_timing_fields(plan)


def test_fake_proposal_never_contains_transcript_but_plan_is_exact():
    script = "WILL: Yes,  I, uh... I suppose."
    anchors = build_transcript_anchor_model(script, target_character="WILL")
    proposal_text = PROPOSAL.replace("w0004-w0009", "w0004-w0005").replace("GAZE-B", "GAZE-A")
    assert "Yes," not in proposal_text and "suppose" not in proposal_text.lower()
    plan = build_performance_plan_from_proposal(
        parse_performance_proposal(proposal_text, vocabulary=VOCAB),
        anchor_model=anchors,
        sequence_id="exact",
    )
    assert "".join(event["span"]["text"] for event in plan["events"]) == "Yes,  I, uh... I suppose."
    assert plan["diagnostics"]["errors"] == []
    assert not any("xml" in value.lower() or "transcript mismatch" in value.lower() for value in plan["diagnostics"]["warnings"])
