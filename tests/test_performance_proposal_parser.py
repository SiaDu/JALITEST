from __future__ import annotations

import pytest

from expregaze_jali.performance_proposal_parser import (
    ProposalValidationError,
    SemanticVocabulary,
    parse_performance_proposal,
    validate_proposal_anchors,
)
from expregaze_jali.transcript_anchor_model import build_transcript_anchor_model


VOCAB = SemanticVocabulary(
    affect_states={"friendly": "Friendly", "nervous": "Nervous", "thinking": "Thinking"},
    heart_states={"angry": "Angry", "sad": "Sad"},
)


def proposal_text(spans=("w0001-w0003", "w0004-w0009"), **changes):
    fields = {
        "intent": "Withhold the insult", "affect": "friendly-66", "heart": "angry-20",
        "gaze": "gaze-b", "head": "medium", "lid": "-1", "blink": "slow_blink",
        "blink_suppression": "none",
    }
    fields.update(changes)
    blocks = []
    for index, span in enumerate(spans, 1):
        lines = [f"S{index:02d}", f"span: {span}"]
        lines.extend(f"{key}: {value}" for key, value in fields.items())
        blocks.append("\n".join(lines))
    reasons = "\n".join(f"S{index:02d}.intent: Intent reason {index}." for index in range(1, len(spans) + 1))
    performance = "\n\n".join(blocks)
    return f"[ANALYZE]\nCareful acting.\n\n[PERFORMANCE]\n\n{performance}\n\n[REASONS]\n\n{reasons}\n"


def test_semantic_normalization_and_complete_fields():
    parsed = parse_performance_proposal(proposal_text(), vocabulary=VOCAB)
    phrase = parsed["phrases"][0]
    assert phrase["intent"] == "WITHHOLD_THE_INSULT"
    assert phrase["affect"] == "Friendly-66"
    assert phrase["heart"] == "Angry-20"
    assert phrase["gaze"] == "GAZE-B"
    assert phrase["head"] == "MEDIUM"
    assert phrase["lid"] == -1
    assert phrase["blink"] == "SLOW_BLINK"
    assert phrase["blink_suppression"] == "NONE"


def test_every_semantic_field_is_required_and_intent_cannot_be_none():
    missing_lid = proposal_text().replace("lid: -1\n", "")
    with pytest.raises(ProposalValidationError, match="S01: Missing required fields: lid"):
        parse_performance_proposal(missing_lid, vocabulary=VOCAB)
    with pytest.raises(ProposalValidationError, match="S01: intent must be a non-NONE"):
        parse_performance_proposal(proposal_text(intent="NONE"), vocabulary=VOCAB)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"affect": "DepressedExistentially-73"}, 'Unknown affect state "DepressedExistentially"'),
        ({"heart": "lonely-20"}, 'Unknown heart state "lonely"'),
        ({"gaze": "GAZE-MYSTERY"}, 'Unknown gaze target "MYSTERY"'),
        ({"head": "BIG"}, "Unknown head value"),
        ({"lid": "-8"}, "Unsupported lid value"),
        ({"blink": "WINK"}, "Unknown blink value"),
        ({"blink_suppression": "MAYBE"}, "Unknown blink_suppression value"),
    ],
)
def test_unknown_semantics_fail_with_phrase_specific_error(change, message):
    with pytest.raises(ProposalValidationError, match=message):
        parse_performance_proposal(proposal_text(**change), vocabulary=VOCAB)


def test_intra_utterance_anchor_resolution_preserves_exact_substrings():
    script = "WILL: Yes, I, uh... I suppose the air was fresh..."
    model = build_transcript_anchor_model(script, target_character="WILL")
    parsed = parse_performance_proposal(proposal_text(), vocabulary=VOCAB)
    resolved = validate_proposal_anchors(parsed, model)
    assert [phrase["text"] for phrase in resolved] == ["Yes, I, uh... ", "I suppose the air was fresh..."]
    assert script[resolved[0]["char_start"]:resolved[0]["char_end"]] == "Yes, I, uh... "


@pytest.mark.parametrize(
    ("spans", "message"),
    [
        (("w0099-w0099",), "unknown anchor w0099"),
        (("w0003-w0001", "w0004-w0009"), "reversed anchor range"),
        (("w0001-w0004", "w0005-w0009"), "crosses dialogue-turn boundary"),
        (("w0004-w0006",), "another character's dialogue"),
        (("w0001-w0003", "w0003-w0003", "w0007-w0009"), "overlaps S01"),
        (("w0001-w0002", "w0007-w0009"), "uncovered anchors"),
    ],
)
def test_anchor_validation_failures(spans, message):
    model = build_transcript_anchor_model("A: one two three\nB: four five six\nA: seven eight nine", target_character="A")
    parsed = parse_performance_proposal(proposal_text(spans=spans, heart="NONE", gaze="NONE"), vocabulary=VOCAB)
    with pytest.raises(ProposalValidationError, match=message):
        validate_proposal_anchors(parsed, model)


def test_complete_target_coverage_accepts_multiple_turns_without_other_speaker():
    model = build_transcript_anchor_model("A: one two three\nB: four five six\nA: seven eight nine", target_character="A")
    parsed = parse_performance_proposal(
        proposal_text(spans=("w0001-w0003", "w0007-w0009"), heart="NONE", gaze="NONE"),
        vocabulary=VOCAB,
    )
    resolved = validate_proposal_anchors(parsed, model)
    assert [phrase["text"] for phrase in resolved] == ["one two three", "seven eight nine"]
