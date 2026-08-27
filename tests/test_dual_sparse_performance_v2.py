from __future__ import annotations

import pytest

from expregaze_jali.dual_performance_plan_v2 import build_dual_performance_plan_v2
from expregaze_jali.dual_sparse_performance_proposal_parser import parse_dual_sparse_performance_proposal
from expregaze_jali.performance_proposal_parser import ProposalValidationError, load_semantic_vocabulary
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model
from expregaze_jali.generate_dual_performance_plan import build_dual_generation_prompt


MODEL = build_conversation_anchor_model("ALICE: Hello there.\nBOB: No.", character_a="ALICE", character_b="BOB")


def parse(body: str):
    return parse_dual_sparse_performance_proposal(
        "[ANALYZE]\ntest\n[INITIAL]\nALICE\naffect: Watchful-80\ngaze: GAZE-BOB\nreason: Enters attentive.\n\nBOB\naffect: Nervous-60\ngaze: GAZE-ALICE\nreason: Enters guarded.\n[CHANGES]\n" + body,
        vocabulary=load_semantic_vocabulary(), anchor_model=MODEL,
    )


def test_sparse_independent_tracks_and_resets():
    proposal = parse("""E001
actor: ALICE
anchor: w0001
gaze: GAZE-BOB
reason: Looks directly.

E002
actor: BOB
anchor: w0002
affect: Happy-120
gaze: GLANCE-UP_LEFT
head: HEAD-TILT_LEFT-SUBTLE
blink: DOUBLE_BLINK
reason: Reacts.

E003
actor: BOB
anchor: w0003
affect: MASK-NONE
gaze: GAZE-DOWN
head: HEAD-NONE
reason: Releases the response.""")
    plan = build_dual_performance_plan_v2(proposal, anchor_model=MODEL, sequence_id="test")
    assert plan["schema_version"] == "dual_performance_plan_v2"
    assert plan["initial_states"]["ALICE"] == {"affect": "Watchful-80", "gaze": "GAZE-BOB", "head": "HEAD-NONE"}
    assert len(plan["tracks"]["ALICE"]) == 1 and len(plan["tracks"]["BOB"]) == 2
    assert plan["tracks"]["ALICE"][0]["changes"] == {"gaze": "GAZE-BOB"}
    assert plan["tracks"]["BOB"][1]["changes"]["affect"] == "MASK-NONE"


@pytest.mark.parametrize("line", [
    "heart: Happy-80", "lid: 2", "blink_suppression: NONE",
    "head: HEAD-LOW", "gaze: RIGHT", "gaze: AVERT-RIGHT", "affect: Happy-0",
])
def test_v2_rejects_removed_or_invalid_semantics(line):
    with pytest.raises(ProposalValidationError):
        parse(f"E001\nactor: ALICE\nanchor: w0001\n{line}\nreason: invalid")


def test_v2_rejects_unknown_actor_anchor_and_empty_event():
    for body in (
        "E001\nactor: CAROL\nanchor: w0001\ngaze: GAZE-BOB",
        "E001\nactor: ALICE\nanchor: w9999\ngaze: GAZE-BOB",
        "E001\nactor: ALICE\nanchor: w0001\nreason: empty",
    ):
        with pytest.raises(ProposalValidationError):
            parse(body)


def test_v2_prompt_treats_aversion_and_thinking_as_motivation_only():
    prompt = build_dual_generation_prompt(script="ALICE: Hello there.\nBOB: No.", character_a="ALICE", character_b="BOB")
    assert "avoiding eye contact" in prompt and "thinking" in prompt and "recalling" in prompt
    assert "GAZE-NONE, GLANCE-NONE, and AVERT are never executable authored gaze modes" in prompt
    assert "gaze: GAZE-DOWN" in prompt and "gaze: GLANCE-UP_LEFT" in prompt
    assert "Do not map an emotion or motivation to a fixed direction" in prompt
    assert not __import__("re").search(r"gaze:\s*AVERT-", prompt)
    assert "Listeners may react during another actor's utterance" in prompt
    assert "earliest semantically sufficient heard cue word" in prompt
    assert "Do not automatically wait for sentence completion, dialogue-turn completion" in prompt
    assert "Both actors enter the scene already performing" in prompt and "Initial affect may not be `MASK-NONE`" in prompt
    assert "There is no fixed event count" in prompt


def test_initial_state_requires_visible_affect_and_reason_and_rejects_blink():
    source = "[ANALYZE]\nx\n[INITIAL]\nALICE\naffect: Happy-120\ngaze: GAZE-BOB\nreason: Enters openly.\n\nBOB\naffect: Neutral-60\ngaze: GAZE-ALICE\nreason: Enters composed.\n[CHANGES]\n"
    proposal = parse_dual_sparse_performance_proposal(source, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    assert proposal["initial_states"]["ALICE"] == {"affect": "Happy-120", "gaze": "GAZE-BOB", "head": "HEAD-NONE"}
    assert proposal["initial_states"]["BOB"] == {"affect": "Neutral-60", "gaze": "GAZE-ALICE", "head": "HEAD-NONE"}
    assert proposal["initial_reasons"]["ALICE"] == "Enters openly."


def test_authored_gaze_none_and_missing_initial_gaze_are_rejected():
    source = "[ANALYZE]\nx\n[INITIAL]\nALICE\naffect: Happy-80\ngaze: GAZE-NONE\nreason: x\nBOB\naffect: Neutral-60\ngaze: GAZE-ALICE\nreason: x\n[CHANGES]\n"
    with pytest.raises(ProposalValidationError, match="GAZE-NONE"):
        parse_dual_sparse_performance_proposal(source, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="GLANCE-NONE"):
        parse_dual_sparse_performance_proposal(source.replace("GAZE-NONE", "GLANCE-NONE"), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    source = source.replace("gaze: GAZE-NONE\n", "", 1)
    with pytest.raises(ProposalValidationError, match="gaze is required"):
        parse_dual_sparse_performance_proposal(source, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    source = "[ANALYZE]\nx\n[INITIAL]\nALICE\naffect: Happy-120\ngaze: GAZE-BOB\nreason: Enters openly.\n\nBOB\naffect: Neutral-60\ngaze: GAZE-ALICE\nreason: Enters composed.\n[CHANGES]\n"
    with pytest.raises(ProposalValidationError, match="initial channel blink is not allowed"):
        parse_dual_sparse_performance_proposal(source.replace("affect: Happy-120", "blink: BLINK"), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="initial gaze must be persistent"):
        parse_dual_sparse_performance_proposal(source.replace("gaze: GAZE-ALICE", "gaze: GLANCE-DOWN"), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="requires one explicit actor block"):
        parse_dual_sparse_performance_proposal(source.replace("\nBOB\naffect: Neutral-60\ngaze: GAZE-ALICE\nreason: Enters composed.", ""), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="affect is required"):
        parse_dual_sparse_performance_proposal(source.replace("affect: Neutral-60\n", ""), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="visible Mask"):
        parse_dual_sparse_performance_proposal(source.replace("affect: Neutral-60", "affect: MASK-NONE"), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="reason is required"):
        parse_dual_sparse_performance_proposal(source.replace("reason: Enters composed.", "reason:  "), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)


@pytest.mark.parametrize("blink", ["SLOW_BLINK", "DOUBLE_BLINK", "EYE_CLOSE_HOLD", "EYE_OPEN"])
def test_v2_authored_blink_vocabulary_accepts_only_explicit_performative_commands(blink):
    assert parse(f"E001\nactor: ALICE\nanchor: w0001\nblink: {blink}\nreason: A deliberate eye action.")["events"][0]["changes"]["blink"] == blink
    with pytest.raises(ProposalValidationError, match="Invalid performative blink"):
        parse("E001\nactor: ALICE\nanchor: w0001\nblink: BLINK\nreason: Invalid authored tag.")
