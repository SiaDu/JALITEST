from __future__ import annotations

import pytest

from expregaze_jali.dual_semantic_beat_parser import parse_dual_semantic_beats
from expregaze_jali.performance_proposal_parser import ProposalValidationError, load_semantic_vocabulary
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model


MODEL = build_conversation_anchor_model("MARTY: Look.\nDION: Yes.", character_a="MARTY", character_b="DION")
BASE = """[INITIAL]
MARTY
affect: Watchful-80
focus: DION
acting: He assesses DION.

DION
affect: Nervous-65
focus: MARTY
acting: She remains guarded.
[BEATS]
E001
actor: MARTY
trigger: w0001
acting: He checks Rachel's reaction.
eye_action: brief_check RACHEL
head: HEAD-DOWN-SUBTLE
blink: SLOW_BLINK
"""


def test_parser_accepts_focus_and_transient_eye_action_grammar():
    ir = parse_dual_semantic_beats(BASE, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    assert ir["initial"]["MARTY"]["focus"] == "DION"
    assert ir["beats"][0]["eye_action"] == {"action": "brief_check", "target": "RACHEL"}
    assert ir["beats"][0]["head"] == "HEAD-DOWN-SUBTLE"


def test_parser_drops_valid_acting_only_beats_without_renumbering_neighbors():
    source = BASE.replace(
        "E001\nactor: MARTY\ntrigger: w0001\nacting: He checks Rachel's reaction.\neye_action: brief_check RACHEL\nhead: HEAD-DOWN-SUBTLE\nblink: SLOW_BLINK",
        "E013\nactor: MARTY\ntrigger: w0001\nacting: He reacts visibly.\naffect: Happy-80\n\n"
        "E014\nactor: DION\ntrigger: w0002\nacting: Dion adds the hand-drawn detail with modest pride.\n\n"
        "E015\nactor: MARTY\ntrigger: w0002\nacting: He looks back to Dion.\nfocus: DION",
    )
    ir = parse_dual_semantic_beats(source, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    assert [beat["event_id"] for beat in ir["beats"]] == ["E013", "E015"]
    assert ir["diagnostics"]["warnings"] == [
        "E014: dropped acting-only beat because it contains no semantic changes"
    ]


def test_parser_still_rejects_missing_acting_when_a_semantic_change_is_present():
    source = BASE.replace("acting: He checks Rachel's reaction.", "", 1)
    with pytest.raises(ProposalValidationError, match="acting are required"):
        parse_dual_semantic_beats(source, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)


def test_parser_allows_mask_none_only_for_later_beats():
    beat_none = BASE.replace("eye_action: brief_check RACHEL", "affect: MASK-NONE")
    ir = parse_dual_semantic_beats(beat_none, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    assert ir["beats"][0]["affect"] == "MASK-NONE"

    initial_none = BASE.replace("Watchful-80", "MASK-NONE", 1)
    with pytest.raises(ProposalValidationError, match="MASK-NONE is not allowed"):
        parse_dual_semantic_beats(initial_none, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)


@pytest.mark.parametrize(("text", "match"), [
    (BASE.replace("actor: MARTY", "actor: RACHEL"), "Unknown performance actor"),
    (BASE.replace("MARTY\naffect", "RACHEL\naffect"), r"Unknown \[INITIAL\] performance actor"),
    (BASE.replace("trigger: w0001", "trigger: w9999"), "Unknown trigger anchor"),
    (BASE.replace("Watchful-80", "Proud-80"), "Unknown Mask state"),
    (BASE.replace("brief_check RACHEL", "inspect RACHEL"), "eye_action must"),
    (BASE.replace("brief_check RACHEL", "brief_check FRONT DOOR"), "Invalid eye_action target"),
    (BASE.replace("focus: DION", "focus: target"), '"TARGET" is a reserved placeholder'),
    (BASE.replace("brief_check RACHEL", "brief_check TARGET"), '"TARGET" is a reserved placeholder'),
    (BASE.replace("focus: DION", "attention: hold DION"), "Unknown initial field attention"),
    (BASE.replace("focus: DION\n", ""), "focus is required"),
    (BASE.replace("focus: DION", "eye_action: brief_check DOWN"), "Unknown initial field eye_action"),
    (BASE.replace("eye_action: brief_check RACHEL", "focus: WILL\neye_action: brief_check DOWN"), "focus and eye_action cannot both"),
    (BASE.replace("acting: He checks Rachel's reaction.", "acting: "), "acting are required"),
])
def test_parser_keeps_structure_and_vocabularies_strict(text, match):
    with pytest.raises(ProposalValidationError, match=match):
        parse_dual_semantic_beats(text, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
