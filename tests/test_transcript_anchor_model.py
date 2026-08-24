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


@pytest.mark.parametrize("script", [
    "<heart01=Happy-28>AGNES: The Latin tutor.</heart01>",
    "AGNES: <m01=Friendly-50>Good day.</m01>",
])
def test_legacy_annotation_tags_are_rejected_before_anchor_generation(script):
    with pytest.raises(ValueError, match="Input Script contains performance annotation tags"):
        build_transcript_anchor_model(script, target_character="AGNES")


def test_multiple_speaker_labels_on_one_line_are_rejected_but_newline_turns_work():
    with pytest.raises(ValueError, match="Multiple dialogue turns were found on one line"):
        build_transcript_anchor_model(
            "AGNES: Good day, sir. AGNES: What brings you to Hewlands?", target_character="AGNES"
        )
    model = build_transcript_anchor_model(
        "AGNES: Good day, sir.\nAGNES: What brings you to Hewlands.", target_character="AGNES"
    )
    assert [turn.turn_id for turn in model.turns] == ["T01", "T02"]


def test_clean_dialogue_with_ordinary_angle_brackets_passes_preflight():
    model = build_transcript_anchor_model("AGNES: Is <3 still your lucky number?", target_character="AGNES")
    assert model.turns[0].utterance_text == "Is <3 still your lucky number?"
