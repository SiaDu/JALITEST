from __future__ import annotations

import pytest

from expregaze_jali.performance_proposal_parser import (
    ProposalValidationError,
    SemanticVocabulary,
    parse_performance_proposal,
    validate_and_resolve_proposal_targets,
    validate_proposal_anchors,
)
from expregaze_jali.transcript_anchor_model import build_transcript_anchor_model


VOCAB = SemanticVocabulary(
    affect_states={
        "friendly": "Friendly", "nervous": "Nervous", "thinking": "Thinking",
        "neutral": "Neutral", "smug": "Smug", "watchful": "Watchful",
    },
    heart_states={"angry": "Angry", "sad": "Sad", "happy": "Happy"},
)


def proposal_text(starts=("w0001", "w0004"), **changes):
    fields = {
        "intent": "Withhold the insult", "affect": "friendly-66", "heart": "angry-20",
        "gaze": "gaze-b", "head": "medium", "lid": "-1", "blink": "slow_blink",
        "blink_suppression": "none",
    }
    fields.update(changes)
    blocks = []
    for index, start in enumerate(starts, 1):
        lines = [f"S{index:02d}", f"start: {start}"]
        lines.extend(f"{key}: {value}" for key, value in fields.items())
        blocks.append("\n".join(lines))
    reasons = "\n".join(f"S{index:02d}.intent: Intent reason {index}." for index in range(1, len(starts) + 1))
    performance = "\n\n".join(blocks)
    return f"[ANALYZE]\nCareful acting.\n\n[PERFORMANCE]\n\n{performance}\n\n[REASONS]\n\n{reasons}\n"


def test_semantic_normalization_and_complete_fields():
    parsed = parse_performance_proposal(proposal_text(), vocabulary=VOCAB)
    phrase = parsed["phrases"][0]
    assert phrase["intent"] == "WITHHOLD_THE_INSULT"
    assert phrase["start_anchor"] == "w0001"
    assert "end_anchor" not in phrase
    assert phrase["affect"] == "Friendly-66"
    assert phrase["heart"] == "Angry-20"
    assert phrase["gaze"] == "GAZE-B"
    assert phrase["head"] == "MEDIUM"
    assert phrase["lid"] == -1
    assert phrase["blink"] == "SLOW_BLINK"
    assert phrase["blink_suppression"] == "NONE"


def test_reasons_accept_block_style_mixed_fields_and_keep_performance_intent():
    text = proposal_text(starts=("w0001", "w0004"))
    text = text.replace(
        "S01.intent: Intent reason 1.\nS02.intent: Intent reason 2.",
        "S01\n"
        "intent: A natural-language explanation of the first beat.\n"
        "affect: The friendly delivery is outwardly reassuring.\n"
        "S02\n"
        "intent: SECOND_BEAT_LABEL\n"
        "S02.affect: The visible affect changes with the second beat.\n"
        "gaze: The attention remains on the listener.",
    )

    parsed = parse_performance_proposal(text, vocabulary=VOCAB)

    assert parsed["reasons"]["S01"]["affect"].startswith("The friendly")
    assert parsed["reasons"]["S02"]["gaze"].startswith("The attention")
    assert parsed["phrases"][1]["intent"] == "WITHHOLD_THE_INSULT"
    assert "S02: intent rationale looks like a label rather than an explanation" in parsed["diagnostics"]["warnings"]


def test_reasons_keep_legacy_inline_format_and_reject_bad_block_references():
    assert parse_performance_proposal(proposal_text(starts=("w0001",)), vocabulary=VOCAB)["reasons"]

    with pytest.raises(ProposalValidationError, match="Reason refers to unknown phrase S99"):
        parse_performance_proposal(
            proposal_text(starts=("w0001",)).replace("S01.intent", "S99.intent"),
            vocabulary=VOCAB,
        )
    with pytest.raises(ProposalValidationError, match="Duplicate reason for affect"):
        parse_performance_proposal(
            proposal_text(starts=("w0001",)).replace(
                "S01.intent: Intent reason 1.",
                "S01\naffect: first reason\nS01.affect: duplicate reason",
            ),
            vocabulary=VOCAB,
        )


@pytest.mark.parametrize("heart", ["Nothing", "nothing", "NONE", "Nothing-0", "Nothing-zero"])
def test_nothing_heart_alias_normalizes_to_inactive_none(heart):
    parsed = parse_performance_proposal(proposal_text(starts=("w0001",), heart=heart), vocabulary=VOCAB)
    assert parsed["phrases"][0]["heart"] == "NONE"


def test_nothing_affect_alias_is_inactive_but_neutral_is_real_affect():
    inactive = parse_performance_proposal(
        proposal_text(starts=("w0001",), affect="Nothing"), vocabulary=VOCAB
    )
    neutral = parse_performance_proposal(
        proposal_text(starts=("w0001",), affect="Neutral-50"), vocabulary=VOCAB
    )
    assert inactive["phrases"][0]["affect"] == "NONE"
    assert neutral["phrases"][0]["affect"] == "Neutral-50"


def test_nothing_with_nonzero_intensity_is_rejected_clearly():
    with pytest.raises(ProposalValidationError, match="use NONE for an inactive channel"):
        parse_performance_proposal(
            proposal_text(starts=("w0001",), heart="Nothing-70"), vocabulary=VOCAB
        )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("affect", "Smug-thirty", "Smug-30"),
        ("affect", "Smug-thirty-five", "Smug-35"),
        ("affect", "Friendly-fifty", "Friendly-50"),
        ("heart", "Happy-twenty-eight", "Happy-28"),
        ("affect", "Watchful-sixty-two", "Watchful-62"),
        ("affect", "Nervous-one-hundred", "Nervous-100"),
        ("affect", "Neutral-zero", "Neutral-0"),
        ("affect", "Smug-twenty eight", "Smug-28"),
        ("affect", "Smug-30", "Smug-30"),
        ("affect", "Smug-30%", "Smug-30"),
        ("affect", "Smug-30.0", "Smug-30"),
    ],
)
def test_affect_and_heart_intensities_normalize_digits_and_english_number_words(
    field, value, expected
):
    parsed = parse_performance_proposal(
        proposal_text(starts=("w0001",), **{field: value}), vocabulary=VOCAB
    )
    assert parsed["phrases"][0][field] == expected


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("Smug-30.7", "Invalid affect value"),
        ("Smug-high", "Invalid affect value"),
        ("Smug-strong", "Invalid affect value"),
        ("Smug-a-little", "Invalid affect value"),
        ("Smug-kind-of-30", "Invalid affect value"),
        ("Smug-one-fifty", "Invalid affect value"),
        ("Smug-one-hundred-one", "Invalid affect value"),
        ("Smug-101", "affect intensity must be between 0 and 100"),
    ],
)
def test_invalid_or_out_of_range_affect_intensities_are_rejected(value, message):
    with pytest.raises(ProposalValidationError, match=message):
        parse_performance_proposal(proposal_text(starts=("w0001",), affect=value), vocabulary=VOCAB)


def test_unknown_state_with_a_valid_word_intensity_reports_unknown_state():
    with pytest.raises(ProposalValidationError, match='Unknown affect state "UnknownEmotion"'):
        parse_performance_proposal(
            proposal_text(starts=("w0001",), affect="UnknownEmotion-thirty"), vocabulary=VOCAB
        )


def test_nothing_ends_hidden_affect_without_inheritance():
    from expregaze_jali.performance_plan_from_proposal import build_performance_plan_from_proposal

    text = proposal_text(
        starts=("w0001", "w0004"), heart="Happy-28", affect="Friendly-66", gaze="NONE"
    )
    before, marker, second = text.partition("S02\n")
    text = before + marker + second.replace("heart: Happy-28", "heart: Nothing", 1).replace(
        "affect: Friendly-66", "affect: Nothing", 1
    )
    proposal = parse_performance_proposal(text, vocabulary=VOCAB)
    model = build_transcript_anchor_model("WILL: one two three four five", target_character="WILL")
    plan = build_performance_plan_from_proposal(proposal, anchor_model=model, sequence_id="nothing")
    assert plan["events"][0]["affect"]["hidden"][0]["value"] == "Happy-28"
    assert plan["events"][1]["affect"]["hidden"] == []
    assert plan["events"][1]["affect"]["visible"] == []


def test_every_semantic_field_is_required_and_intent_cannot_be_none():
    missing_lid = proposal_text().replace("lid: -1\n", "")
    with pytest.raises(ProposalValidationError, match="S01: Missing required fields: lid"):
        parse_performance_proposal(missing_lid, vocabulary=VOCAB)
    with pytest.raises(ProposalValidationError, match="S01: intent must be a non-NONE"):
        parse_performance_proposal(proposal_text(intent="NONE"), vocabulary=VOCAB)
    with pytest.raises(ProposalValidationError, match="S01: Unknown field span"):
        parse_performance_proposal(
            proposal_text(starts=("w0001",)).replace("start: w0001", "span: w0001-w0003"),
            vocabulary=VOCAB,
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"affect": "DepressedExistentially-73"}, 'Unknown affect state "DepressedExistentially"'),
        ({"heart": "lonely-20"}, 'Unknown heart state "lonely"'),
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


def test_one_start_automatically_covers_the_rest_of_its_target_turn():
    model = build_transcript_anchor_model("WILL: one two three four", target_character="WILL")
    parsed = parse_performance_proposal(proposal_text(starts=("w0001",)), vocabulary=VOCAB)
    resolved = validate_proposal_anchors(parsed, model)
    assert [phrase["text"] for phrase in resolved] == ["one two three four"]
    assert resolved[0]["char_end"] == model.turns[0].utterance_end


def test_three_or_more_starts_partition_one_turn_without_gaps_or_overlaps():
    script = "WILL: one  two three four five"
    model = build_transcript_anchor_model(script, target_character="WILL")
    parsed = parse_performance_proposal(
        proposal_text(starts=("w0001", "w0002", "w0004")), vocabulary=VOCAB
    )
    resolved = validate_proposal_anchors(parsed, model)
    assert [phrase["text"] for phrase in resolved] == ["one  ", "two three ", "four five"]
    assert "".join(phrase["text"] for phrase in resolved) == model.turns[0].utterance_text
    assert all(
        resolved[index]["char_end"] == resolved[index + 1]["char_start"]
        for index in range(len(resolved) - 1)
    )


@pytest.mark.parametrize(
    ("gaze", "expected_proposal", "expected_canonical"),
    [
        ("GAZE-WILL", "GAZE-B", "GAZE-CHARACTER_WILL"),
        ("GAZE-AGNES", "GAZE-A", "GAZE-CHARACTER_AGNES"),
        ("GAZE-CHARACTER_WILL", "GAZE-B", "GAZE-CHARACTER_WILL"),
        ("GLANCE-WILL", "GLANCE-B", "GLANCE-CHARACTER_WILL"),
    ],
)
def test_known_character_gaze_names_normalize_through_anchor_aliases(
    gaze, expected_proposal, expected_canonical
):
    from expregaze_jali.performance_plan_from_proposal import build_performance_plan_from_proposal

    model = build_transcript_anchor_model("AGNES: one two three\nWILL: four five", target_character="AGNES")
    parsed = parse_performance_proposal(
        proposal_text(starts=("w0001",), gaze=gaze, heart="NONE"), vocabulary=VOCAB
    )
    validate_and_resolve_proposal_targets(parsed, model)
    assert parsed["phrases"][0]["gaze"] == expected_proposal
    plan = build_performance_plan_from_proposal(parsed, anchor_model=model, sequence_id="gaze")
    assert plan["events"][0]["gaze"][0]["value"] == expected_canonical


def test_unknown_character_gaze_target_is_rejected_after_anchor_context_is_known():
    model = build_transcript_anchor_model("AGNES: one two three\nWILL: four five", target_character="AGNES")
    parsed = parse_performance_proposal(
        proposal_text(starts=("w0001",), gaze="GAZE-RANDOM_PERSON", heart="NONE"),
        vocabulary=VOCAB,
    )
    with pytest.raises(ProposalValidationError, match='S01: Unknown character gaze target "RANDOM_PERSON"'):
        validate_and_resolve_proposal_targets(parsed, model)


def test_object_gaze_target_is_semantically_valid_without_maya_mapping():
    model = build_transcript_anchor_model("AGNES: one two three", target_character="AGNES")
    parsed = parse_performance_proposal(
        proposal_text(starts=("w0001",), gaze="GAZE-OBJECT_HAWK", heart="NONE"),
        vocabulary=VOCAB,
    )
    validate_and_resolve_proposal_targets(parsed, model)
    assert parsed["phrases"][0]["gaze"] == "GAZE-OBJECT_HAWK"


@pytest.mark.parametrize(
    ("starts", "message"),
    [
        (("w0099",), "unknown anchor w0099"),
        (("w0002", "w0007"), "T01: first Performance Phrase must start at w0001"),
        (("w0004",), "another character's dialogue"),
        (("w0001", "w0001", "w0007"), "S02: duplicate phrase boundary w0001"),
        (("w0007", "w0001"), "S02: phrase boundaries are not in transcript order"),
        (("w0001",), "Target turn T03 has no Performance Phrase"),
    ],
)
def test_anchor_validation_failures(starts, message):
    model = build_transcript_anchor_model("A: one two three\nB: four five six\nA: seven eight nine", target_character="A")
    parsed = parse_performance_proposal(proposal_text(starts=starts, heart="NONE", gaze="NONE"), vocabulary=VOCAB)
    with pytest.raises(ProposalValidationError, match=message):
        validate_proposal_anchors(parsed, model)


def test_complete_target_coverage_accepts_multiple_turns_without_other_speaker():
    model = build_transcript_anchor_model("A: one two three\nB: four five six\nA: seven eight nine", target_character="A")
    parsed = parse_performance_proposal(
        proposal_text(starts=("w0001", "w0003", "w0007", "w0009"), heart="NONE", gaze="NONE"),
        vocabulary=VOCAB,
    )
    resolved = validate_proposal_anchors(parsed, model)
    assert [phrase["text"] for phrase in resolved] == ["one two ", "three", "seven eight ", "nine"]
    for turn in model.turns:
        if turn.speaker == "A":
            assert "".join(phrase["text"] for phrase in resolved if phrase["turn_id"] == turn.turn_id) == turn.utterance_text
