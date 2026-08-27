from __future__ import annotations

import re

from expregaze_jali.dual_performance_plan_from_proposal import adapt_dual_performance_plan_v0, build_dual_performance_plan_from_proposal
from expregaze_jali.dual_performance_proposal_parser import parse_dual_performance_proposal
from expregaze_jali.generate_dual_performance_plan import build_dual_generation_prompt
from expregaze_jali.performance_proposal_parser import load_semantic_vocabulary
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model


def _proposal() -> str:
    return """[ANALYZE]\nx\n[PERFORMANCE]\nS01\nstart: w0001\nALICE.affect: Happy-80\nALICE.gaze: GAZE-BOB\nALICE.head: NONE\nALICE.lid: NONE\nALICE.blink: NONE\nALICE.blink_suppression: NONE\nBOB.affect: Nervous-120\nBOB.gaze: AVERT-ALICE\nBOB.head: NONE\nBOB.lid: NONE\nBOB.blink: NONE\nBOB.blink_suppression: NONE\nintent: TEST\nS02\nstart: w0002\nALICE.affect: Happy-80\nALICE.gaze: GAZE-BOB\nALICE.head: NONE\nALICE.lid: NONE\nALICE.blink: NONE\nALICE.blink_suppression: NONE\nBOB.affect: Nervous-120\nBOB.gaze: AVERT-ALICE\nBOB.head: NONE\nBOB.lid: NONE\nBOB.blink: NONE\nBOB.blink_suppression: NONE\nintent: TEST\n[REASONS]\nS01\nintent: x\nS02\nintent: x"""


def test_named_mask_only_prompt_parser_and_plan():
    prompt = build_dual_generation_prompt(script="ALICE: hi\nBOB: yo", character_a="ALICE", character_b="BOB")
    assert "ALICE.affect" in prompt and "BOB.affect" in prompt and ".heart" not in prompt
    assert not re.search(r"(?m)^A\.affect", prompt) and not re.search(r"GAZE-A(?:\s|$)", prompt) and "Trace=5" in prompt and "Ludicrous=200" in prompt
    vocabulary = load_semantic_vocabulary(); parsed = parse_dual_performance_proposal(_proposal(), vocabulary=vocabulary, character_names=("ALICE", "BOB"))
    assert parsed["phrases"][0]["states"]["ALICE"]["affect"] == "Happy-80"
    plan = build_dual_performance_plan_from_proposal(parsed, anchor_model=build_conversation_anchor_model("ALICE: hi\nBOB: yo", character_a="ALICE", character_b="BOB"), sequence_id="x")
    assert plan["schema_version"] == "dual_performance_plan_v1" and plan["characters"] == ["ALICE", "BOB"]
    assert plan["phrases"][0]["speaker"] == "ALICE" and set(plan["phrases"][0]["states"]) == {"ALICE", "BOB"}


def test_v0_adapter_is_structural():
    adapted = adapt_dual_performance_plan_v0({"schema_version":"dual_performance_plan_v0", "characters":{"A":"ALICE","B":"BOB"}, "phrases":[{"speaker":"A", "states":{"A":{"affect":"Happy-80"},"B":{}}, "rationale":{"A":{},"B":{}}}]})
    assert adapted["schema_version"] == "dual_performance_plan_v1" and adapted["phrases"][0]["speaker"] == "ALICE" and "ALICE" in adapted["phrases"][0]["states"]
