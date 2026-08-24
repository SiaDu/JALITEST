from __future__ import annotations

import pytest

from expregaze_jali.transcript_anchor_model import build_transcript_anchor_model


def test_labeled_anchor_ids_positions_speakers_aliases_and_whitespace():
    script = "AGNES: Good  day, sir.\nWILL: Good day to you.\n"
    model = build_transcript_anchor_model(script, target_character="AGNES")

    assert model.aliases == {"A": "AGNES", "B": "WILL"}
    assert [turn.speaker for turn in model.turns] == ["AGNES", "WILL"]
    assert [anchor.anchor_id for anchor in model.anchors] == [
        "w0001", "w0002", "w0003", "w0004", "w0005", "w0006", "w0007"
    ]
    assert model.anchors[1].text == "day,"
    assert script[model.anchors[1].char_start:model.anchors[1].char_end] == "day,"
    assert model.turns[0].utterance_text == "Good  day, sir."
    assert "[w0001 Good] [w0002 day,]" in model.anchored_script()


def test_unlabeled_script_is_one_target_owned_turn_with_exact_offsets():
    script = "  Yes, I, uh...\nI suppose.  "
    model = build_transcript_anchor_model(script, target_character="WILL")

    assert model.aliases == {"A": "WILL"}
    assert len(model.turns) == 1
    assert model.turns[0].utterance_text == script
    assert model.turns[0].utterance_start == 0
    assert model.turns[0].utterance_end == len(script)
    assert script[model.anchors[-1].char_start:model.anchors[-1].char_end] == "suppose."


def test_labeled_script_rejects_mixed_unlabeled_lines_and_more_than_two_speakers():
    with pytest.raises(ValueError, match="every non-empty line"):
        build_transcript_anchor_model("A: Hi\ncontinuation", target_character="A")
    with pytest.raises(ValueError, match="at most two"):
        build_transcript_anchor_model("A: Hi\nB: Hi\nC: Hi", target_character="A")
