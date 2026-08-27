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
        "[ANALYZE]\ntest\n[CHANGES]\n" + body,
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
gaze: GAZE-NONE
head: HEAD-NONE
reason: Releases the response.""")
    plan = build_dual_performance_plan_v2(proposal, anchor_model=MODEL, sequence_id="test")
    assert plan["schema_version"] == "dual_performance_plan_v2"
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
    assert "AVERT is never an executable gaze mode" in prompt
    assert "gaze: GAZE-DOWN" in prompt and "gaze: GLANCE-UP_LEFT" in prompt
    assert "Do not map an emotion or motivation to a fixed direction" in prompt
    assert not __import__("re").search(r"gaze:\s*AVERT-", prompt)
