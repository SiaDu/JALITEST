from __future__ import annotations

import pytest

from expregaze_jali.dual_semantic_beat_parser import parse_dual_semantic_beats
from expregaze_jali.performance_proposal_parser import ProposalValidationError, load_semantic_vocabulary
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model


MODEL = build_conversation_anchor_model("MARTY: Look.\nDION: Yes.", character_a="MARTY", character_b="DION")
BASE = """[INITIAL]
MARTY
affect: Watchful-80
attention: hold DION
acting: He assesses DION.

DION
affect: Nervous-65
attention: hold MARTY
acting: She remains guarded.
[BEATS]
E001
actor: MARTY
trigger: w0001
acting: He checks Rachel's reaction.
attention: brief_check RACHEL
head: HEAD-DOWN-SUBTLE
blink: SLOW_BLINK
"""


def test_parser_accepts_small_attention_grammar_and_non_actor_target():
    ir = parse_dual_semantic_beats(BASE, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    assert ir["initial"]["MARTY"]["attention"] == {"action": "hold", "target": "DION"}
    assert ir["beats"][0]["attention"] == {"action": "brief_check", "target": "RACHEL"}
    assert ir["beats"][0]["head"] == "HEAD-DOWN-SUBTLE"


@pytest.mark.parametrize(("text", "match"), [
    (BASE.replace("actor: MARTY", "actor: RACHEL"), "Unknown performance actor"),
    (BASE.replace("MARTY\naffect", "RACHEL\naffect"), r"Unknown \[INITIAL\] performance actor"),
    (BASE.replace("trigger: w0001", "trigger: w9999"), "Unknown trigger anchor"),
    (BASE.replace("Watchful-80", "Proud-80"), "Unknown Mask state"),
    (BASE.replace("brief_check RACHEL", "inspect RACHEL"), "attention must"),
    (BASE.replace("brief_check RACHEL", "brief_check FRONT DOOR"), "Invalid attention target"),
    (BASE.replace("acting: He checks Rachel's reaction.", "acting: "), "acting are required"),
])
def test_parser_keeps_structure_and_vocabularies_strict(text, match):
    with pytest.raises(ProposalValidationError, match=match):
        parse_dual_semantic_beats(text, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
