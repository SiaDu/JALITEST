from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from expregaze_jali.dual_performance_plan_from_proposal import (
    build_dual_performance_plan_from_proposal,
    resolve_dual_phrase_boundaries,
)
from expregaze_jali.dual_performance_proposal_parser import parse_dual_performance_proposal
from expregaze_jali.generate_dual_performance_plan import (
    build_dual_generation_prompt,
    generate_dual_performance_plan,
)
from expregaze_jali.performance_proposal_parser import ProposalValidationError, SemanticVocabulary
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from backend_process_runner import prepare_generation_command  # noqa: E402
from performance_score_model import (  # noqa: E402
    DualPerformanceScoreModel,
    format_rationale_view,
)
from performance_plan_ui_data import load_performance_plan  # noqa: E402


SCRIPT = (
    "AGNES: What brought you into the garden?\n"
    "WILL: Yes, I, uh... I suppose the air was fresh out here, and... uh, I saw you with your bird.\n"
    "AGNES: It's a hawk."
)
VOCAB = SemanticVocabulary(
    affect_states={
        "watchful": "Watchful", "nervous": "Nervous",
        "thinking": "Thinking", "friendly": "Friendly", "polite": "Polite",
    },
    heart_states={"happy": "Happy", "nervous": "Nervous"},
)


def dual_proposal() -> str:
    starts = (
        ("w0001", "ASK_THE_QUESTION", "Watchful-55", "GAZE-WILL", "Nervous-60", "AVERT-DOWN"),
        ("w0007", "HESITATE_BEFORE_ANSWERING", "Watchful-55", "GAZE-B", "Nervous-sixty", "AVERT-DOWN"),
        ("w0010", "CONSTRUCT_A_SAFE_EXPLANATION", "Thinking-45", "GAZE-B", "Thinking-55", "AVERT-UP_RIGHT"),
        ("w0019", "REVEAL_THE_REAL_REASON", "Thinking-60", "GAZE-WILL", "Friendly-50", "GAZE-AGNES"),
        ("w0026", "NAME_THE_BIRD", "Watchful-40", "GAZE-B", "Friendly-45", "GAZE-A"),
    )
    blocks = []
    reasons = []
    for index, (start, intent, a_affect, a_gaze, b_affect, b_gaze) in enumerate(starts, 1):
        pid = f"S{index:02d}"
        blocks.append(
            f"{pid}\nstart: {start}\nintent: {intent}\n"
            f"A.affect: {a_affect}\nA.heart: Nothing\nA.gaze: {a_gaze}\n"
            "A.head: NONE\nA.lid: NONE\nA.blink: NONE\nA.blink_suppression: NONE\n"
            f"B.affect: {b_affect}\nB.heart: Happy-twenty-eight\nB.gaze: {b_gaze}\n"
            "B.head: LOW\nB.lid: -1\nB.blink: NONE\nB.blink_suppression: NONE"
        )
        reasons.extend((
            f"{pid}.intent: Shared beat {index}.",
            f"{pid}.A.affect: A reaction {index}.",
            f"{pid}.A.gaze: A watches {index}.",
            f"{pid}.A.head: A remains still {index}.",
            f"{pid}.B.affect: B reaction {index}.",
            f"{pid}.B.heart: B feels warmth {index}.",
            f"{pid}.B.gaze: B directs attention {index}.",
            f"{pid}.B.head: B responds {index}.",
            f"{pid}.B.lid: B lid choice {index}.",
        ))
    return (
        "[ANALYZE]\nAgnes tests Will while Will gradually reveals his motive.\n\n"
        "[PERFORMANCE]\n\n" + "\n\n".join(blocks) +
        "\n\n[REASONS]\n" + "\n".join(reasons) + "\n"
    )


def build_plan() -> dict:
    model = build_conversation_anchor_model(SCRIPT, character_a="AGNES", character_b="WILL")
    proposal = parse_dual_performance_proposal(dual_proposal(), vocabulary=VOCAB)
    return build_dual_performance_plan_from_proposal(
        proposal, anchor_model=model, sequence_id="dual_test", proposal_path="proposal.txt"
    )


def test_shared_conversation_anchors_preserve_aliases_speakers_and_exact_positions():
    model = build_conversation_anchor_model(SCRIPT, character_a="AGNES", character_b="WILL")
    assert model.aliases == {"A": "AGNES", "B": "WILL"}
    assert [turn.speaker for turn in model.turns] == ["AGNES", "WILL", "AGNES"]
    assert model.script[model.anchors[6].char_start:model.anchors[6].char_end] == "Yes,"
    assert model.anchors[18].text == "uh,"
    assert model.anchor_map()["format"] == "conversation_anchor_v1"


def test_conversation_anchor_rejects_unlabeled_and_unknown_speakers():
    with pytest.raises(ValueError, match="speaker-labeled"):
        build_conversation_anchor_model("Hello there.", character_a="AGNES", character_b="WILL")
    with pytest.raises(ValueError, match="unknown speaker"):
        build_conversation_anchor_model(
            "AGNES: Hello.\nSTRANGER: Hello.", character_a="AGNES", character_b="WILL"
        )


def test_conversation_anchor_does_not_require_both_characters_to_speak():
    model = build_conversation_anchor_model(
        "AGNES: First thought.\nAGNES: Second thought.",
        character_a="AGNES", character_b="WILL",
    )
    assert model.aliases == {"A": "AGNES", "B": "WILL"}
    assert [turn.speaker for turn in model.turns] == ["AGNES", "AGNES"]


def test_dual_proposal_has_complete_normalized_a_b_state_and_shared_intent():
    parsed = parse_dual_performance_proposal(dual_proposal(), vocabulary=VOCAB)
    phrase = parsed["phrases"][1]
    assert phrase["intent"] == "HESITATE_BEFORE_ANSWERING"
    assert phrase["states"]["A"]["heart"] == "NONE"
    assert phrase["states"]["B"]["affect"] == "Nervous-60"
    assert phrase["states"]["B"]["heart"] == "Happy-28"


def test_dual_reasons_accept_real_s10_mixed_block_style_and_do_not_change_execution():
    proposal = dual_proposal()
    performance = proposal.split("[PERFORMANCE]\n\n", 1)[1].split("\n\n[REASONS]", 1)[0]
    blocks = performance.split("\n\n")[:3]
    for old, new in (("S01\n", "S09\n"), ("S02\n", "S10\n"), ("S03\n", "S11\n")):
        blocks = [block.replace(old, new, 1) for block in blocks]
    reasons = "\n".join((
        "S09.intent: The conversation opens with guarded inquiry.",
        "S09.A.affect: Agnes watches closely.",
        "S10",
        "intent: WILL'S_EXPLANATION_REVEALS_AGNES_AS_THE_REAL_ATTRACTION",
        "S10.A.affect: Agnes registers the personal disclosure.",
        "S10.A.heart: Her feeling becomes more immediate.",
        "A.gaze: She holds attention on Will.",
        "S10.B.affect: Will tries to remain composed.",
        "S11",
        "intent: AGNES_IDENTIFIES_THE_HAWK_AS_SAFE_COMMON_GROUND",
        "A.affect: Agnes redirects the exchange safely.",
        "S11.B.affect: Will follows that redirect.",
    ))
    text = "[ANALYZE]\nA shared scene.\n\n[PERFORMANCE]\n\n" + "\n\n".join(blocks) + "\n\n[REASONS]\n" + reasons

    parsed = parse_dual_performance_proposal(text, vocabulary=VOCAB)

    assert parsed["reasons"]["S10"]["A"]["gaze"] == "She holds attention on Will."
    assert parsed["reasons"]["S11"]["B"]["affect"] == "Will follows that redirect."
    assert parsed["phrases"][1]["intent"] == "HESITATE_BEFORE_ANSWERING"
    assert "S10: intent rationale looks like a label rather than an explanation" in parsed["diagnostics"]["warnings"]


def test_dual_reasons_reject_unknown_block_phrase_and_duplicate_fields():
    with pytest.raises(ProposalValidationError, match="Reason refers to unknown phrase S99"):
        parse_dual_performance_proposal(
            dual_proposal().replace("S01.intent", "S99.intent", 1), vocabulary=VOCAB
        )
    with pytest.raises(ProposalValidationError, match="Duplicate reason for A.affect"):
        parse_dual_performance_proposal(
            dual_proposal().replace(
                "S01.intent: Shared beat 1.",
                "S01\nA.affect: initial reason\nS01.A.affect: duplicate reason",
                1,
            ),
            vocabulary=VOCAB,
        )
    with pytest.raises(ProposalValidationError, match="Unknown rationale alias C"):
        parse_dual_performance_proposal(
            dual_proposal().replace("S01.A.affect", "S01.C.affect", 1), vocabulary=VOCAB
        )


def test_dual_proposal_requires_every_a_b_field_and_rejects_unknown_semantics():
    with pytest.raises(ProposalValidationError, match="Missing required A fields: lid"):
        parse_dual_performance_proposal(
            dual_proposal().replace("A.lid: NONE\n", "", 1), vocabulary=VOCAB
        )
    with pytest.raises(ProposalValidationError, match="Unknown B.affect state"):
        parse_dual_performance_proposal(
            dual_proposal().replace("B.affect: Nervous-sixty", "B.affect: Impossible-thirty", 1),
            vocabulary=VOCAB,
        )


def test_shared_boundaries_partition_every_turn_with_no_gaps_and_derive_speaker():
    model = build_conversation_anchor_model(SCRIPT, character_a="AGNES", character_b="WILL")
    proposal = parse_dual_performance_proposal(dual_proposal(), vocabulary=VOCAB)
    resolved = resolve_dual_phrase_boundaries(proposal, model)
    for turn in model.turns:
        rows = [row for row in resolved if row["turn_id"] == turn.turn_id]
        assert rows[0]["char_start"] == turn.utterance_start
        assert rows[-1]["char_end"] == turn.utterance_end
        assert all(left["char_end"] == right["char_start"] for left, right in zip(rows, rows[1:]))
        assert "".join(row["text"] for row in rows) == turn.utterance_text
    plan = build_dual_performance_plan_from_proposal(
        proposal, anchor_model=model, sequence_id="boundaries"
    )
    assert [row["speaker"] for row in plan["phrases"]] == ["A", "B", "B", "B", "A"]
    assert plan["phrases"][1]["span"]["text"] == "Yes, I, uh... "
    assert plan["phrases"][2]["span"]["text"].startswith("I suppose")
    assert plan["phrases"][3]["span"]["text"].startswith("uh, I saw")


def test_dual_boundaries_require_first_anchor_of_every_turn():
    model = build_conversation_anchor_model(SCRIPT, character_a="AGNES", character_b="WILL")
    parsed = parse_dual_performance_proposal(
        dual_proposal().replace("start: w0007", "start: w0008", 1), vocabulary=VOCAB
    )
    with pytest.raises(ProposalValidationError, match="T02: first Performance Phrase must start at w0007"):
        resolve_dual_phrase_boundaries(parsed, model)


def test_dual_plan_schema_preserves_states_rationale_and_has_no_timing_fields():
    plan = build_plan()
    assert plan["schema_version"] == "dual_performance_plan_v0"
    assert plan["characters"] == {"A": "AGNES", "B": "WILL"}
    phrase = plan["phrases"][3]
    assert phrase["intent"] == "REVEAL_THE_REAL_REASON"
    assert phrase["states"]["A"]["gaze"] == "GAZE-B"
    assert phrase["states"]["B"]["gaze"] == "GAZE-A"
    assert phrase["rationale"]["A"]["affect"] == "A reaction 4."
    assert phrase["source_proposal_id"] == "S04"
    assert not any(key in json.dumps(plan).lower() for key in ('"time"', '"frame"', '"duration"'))


def test_dual_known_character_gaze_normalizes_and_only_explicit_unknown_character_fails():
    plan = build_plan()
    assert plan["phrases"][0]["states"]["A"]["gaze"] == "GAZE-B"
    assert plan["phrases"][3]["states"]["B"]["gaze"] == "GAZE-A"
    model = build_conversation_anchor_model(SCRIPT, character_a="AGNES", character_b="WILL")
    parsed = parse_dual_performance_proposal(
        dual_proposal().replace("A.gaze: GAZE-WILL", "A.gaze: GAZE-CHARACTER_RANDOM_PERSON", 1),
        vocabulary=VOCAB,
    )
    with pytest.raises(ProposalValidationError, match='Unknown character gaze target "RANDOM_PERSON"'):
        build_dual_performance_plan_from_proposal(parsed, anchor_model=model, sequence_id="bad")


@pytest.mark.parametrize(("value", "expected"), [
    ("GAZE-WILL", "GAZE-B"), ("GAZE-B", "GAZE-B"),
    ("GAZE-HAWK", "GAZE-HAWK"), ("GAZE-OBJECT_HAWK", "GAZE-OBJECT_HAWK"),
    ("GAZE-CHARACTER_WILL", "GAZE-B"), ("AVERT-DOWN", "AVERT-DOWN"),
])
def test_dual_gaze_targets_remain_semantic_until_animation(value, expected):
    model = build_conversation_anchor_model(SCRIPT, character_a="AGNES", character_b="WILL")
    proposal = parse_dual_performance_proposal(dual_proposal().replace("A.gaze: GAZE-WILL", f"A.gaze: {value}", 1), vocabulary=VOCAB)
    assert resolve_dual_phrase_boundaries(proposal, model)[0]["states"]["A"]["gaze"] == expected


def test_dual_bare_avert_remains_unresolved_until_the_author_edits_it():
    model = build_conversation_anchor_model(SCRIPT, character_a="AGNES", character_b="WILL")
    proposal = parse_dual_performance_proposal(
        dual_proposal()
        .replace("A.gaze: GAZE-WILL", "A.gaze: AVERT", 1)
        .replace("B.gaze: AVERT-DOWN", "B.gaze: AVERT", 1),
        vocabulary=VOCAB,
    )
    resolved = resolve_dual_phrase_boundaries(proposal, model)

    assert resolved[0]["states"]["A"]["gaze"] == "AVERT-UNRESOLVED"
    assert resolved[0]["states"]["B"]["gaze"] == "AVERT-UNRESOLVED"


def test_dual_explicit_avert_directions_remain_unchanged():
    model = build_conversation_anchor_model(SCRIPT, character_a="AGNES", character_b="WILL")
    proposal = parse_dual_performance_proposal(
        dual_proposal().replace("A.gaze: GAZE-WILL", "A.gaze: AVERT-UP_LEFT", 1),
        vocabulary=VOCAB,
    )
    resolved = resolve_dual_phrase_boundaries(proposal, model)

    assert resolved[0]["states"]["A"]["gaze"] == "AVERT-UP_LEFT"
    assert resolved[0]["states"]["B"]["gaze"] == "AVERT-DOWN"


def test_dual_score_exact_form_hides_inactive_state_and_supports_validated_edits():
    model = DualPerformanceScoreModel(build_plan())
    score = model.score_text
    assert "A:<Watchful-55><GAZE-B> | B:<l-1><Nervous-60><HEART-Happy-28><AVERT-DOWN><HEAD-LOW>" in score
    assert "<HEAD-NONE>" not in score
    assert "w000" not in score and "S01" not in score
    assert "   B: Yes, I, uh..." in score
    edited = score.replace("<Watchful-55>", "<Polite-42>", 1)
    edited = edited.replace("<Nervous-60>", "<Friendly-55>", 1)
    edited = edited.replace("<GAZE-B>", "<GLANCE-A>", 1)
    edited = edited.replace("<AVERT-DOWN>", "<GAZE-B>", 1)
    edited = edited.replace("{ASK_THE_QUESTION}", "{CAUTIOUS_SOCIAL_OPENING}", 1)
    applied = model.apply(edited)
    first = applied["phrases"][0]
    assert first["intent"] == "CAUTIOUS_SOCIAL_OPENING"
    assert first["states"]["A"]["affect"] == "Polite-42"
    assert first["states"]["B"]["affect"] == "Friendly-55"
    assert first["states"]["A"]["gaze"] == "GLANCE-A"
    assert first["states"]["B"]["gaze"] == "GAZE-B"
    assert model.is_manually_edited(1)
    assert "AI rationale corresponds to the original proposal" in format_rationale_view(model, 1)
    assert "A reaction 1." in format_rationale_view(model, 1)


def test_dual_score_rejects_dialogue_or_speaker_modification():
    model = DualPerformanceScoreModel(build_plan())
    dialogue = model.score_text.replace("Yes, I, uh...", "No, I, uh...", 1)
    assert any("Dialogue text" in error.message for error in model.validate(dialogue).errors)
    speaker = model.score_text.replace("B: Yes, I, uh...", "A: Yes, I, uh...", 1)
    assert any("Dialogue speaker" in error.message for error in model.validate(speaker).errors)
    empty_intent = model.score_text.replace("{ASK_THE_QUESTION}", "{}", 1)
    assert any("Intent heading" in error.message for error in model.validate(empty_intent).errors)


def test_dual_score_noop_preserves_semantic_object_target_prefix():
    plan = build_plan()
    plan["phrases"][0]["states"]["A"]["gaze"] = "GAZE-OBJECT_HAWK"
    model = DualPerformanceScoreModel(plan)
    assert "<GAZE-HAWK>" in model.score_text
    applied = model.apply(model.score_text)
    assert applied["phrases"][0]["states"]["A"]["gaze"] == "GAZE-OBJECT_HAWK"


def test_dual_prompt_and_generation_use_one_call_and_write_expected_artifacts(tmp_path: Path):
    prompt = build_dual_generation_prompt(
        script=SCRIPT, character_a="AGNES", character_b="WILL"
    )
    assert "Dual Semantic Beat IR v1" in prompt
    assert "[INITIAL]\nAGNES" in prompt
    assert "MASK-NONE" in prompt
    assert "VISIBLE AFFECT — CLOSED VOCABULARY:" in prompt
    assert "HEART" not in prompt
    assert "[BEATS]" in prompt
    assert "GAZE-WILL" not in prompt and "GLANCE-WILL" not in prompt
    assert "Open natural language is allowed only in acting fields" in prompt
    assert "Open natural language is allowed only in reason fields" not in prompt
    assert "Built-in attention directions" in prompt
    assert "Directional gaze targets" not in prompt
    assert "each individual semantic channel is optional, but every beat must contain at least one actual semantic change" in prompt
    assert "Do not output an acting-only beat" in prompt
    assert "A beat may contain one or multiple changed semantic channels" in prompt
    assert "CRITICAL GAZE FIELD EXCLUSIVITY" in prompt
    assert "Never output both `focus:` and `eye_action:` in one beat" in prompt
    assert "In [BEATS], affect may be MASK-NONE when the persistent semantic affect should end" in prompt
    assert "eye_action: brief_check WILL" in prompt
    assert "affect: Nervous-75" in prompt
    assert "affect: Nervous-80\neye_action: brief_check DOWN" in prompt
    assert "eye_action: brief_check DOWN\nhead: HEAD-DOWN-SUBTLE" in prompt
    assert "head: HEAD-NONE" in prompt
    assert "Head is a persistent neck pose" in prompt
    assert "Actively consider head behavior" in prompt
    assert "Do not add head merely to satisfy a quota" in prompt
    assert "Acting Direction: NONE" in prompt
    assert "Context: NONE" not in prompt
    assert "AGNES becomes increasingly curious about WILL." not in prompt
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        proposal_text = "[INITIAL]\nAGNES\naffect: Watchful-80\nfocus: WILL\nacting: Enters watchful.\n\nWILL\naffect: Nervous-60\nfocus: AGNES\nacting: Enters guarded.\n[BEATS]\nE001\nactor: AGNES\ntrigger: w0001\nacting: She becomes more alert and lifts her head.\naffect: Watchful-100\nhead: HEAD-UP-SUBTLE"
        Path(kwargs["output_text"]).write_text(proposal_text, encoding="utf-8")
        Path(kwargs["output_meta"]).write_text('{"status":"completed"}\n', encoding="utf-8")
        return proposal_text, {"status": "completed"}

    plan = generate_dual_performance_plan(
        script=SCRIPT, character_a="AGNES", character_b="WILL", context=None,
        run_id="dual_run", output_dir=tmp_path / "dual_run", proposal_runner=runner,
    )
    assert len(calls) == 1 and plan["schema_version"] == "dual_performance_plan_v2"
    assert plan["tracks"]["AGNES"][0]["changes"] == {
        "affect": "Watchful-100", "head": "HEAD-UP-SUBTLE"
    }
    for name in (
        "input_script.txt", "input_context.txt", "actor_prompt.txt", "anchored_script.txt",
        "anchor_map.json", "semantic_beats.txt", "semantic_beats.json", "performance_proposal.txt", "llm_response_meta.json", "performance_plan.json",
    ):
        assert (tmp_path / "dual_run" / name).exists()


def test_dual_prompt_identity_contract_is_dynamic_and_examples_never_assign_scene_names():
    reversed_prompt = build_dual_generation_prompt(
        script="WILL: Look there.\nAGNES: I see it.", character_a="WILL", character_b="AGNES"
    )
    assert "WILL and AGNES are immutable script identities." in reversed_prompt
    assert reversed_prompt.index("IDENTITY CONTRACT") < reversed_prompt.index("Return exactly [INITIAL], then [BEATS]")
    assert "[INITIAL]\nWILL" in reversed_prompt
    assert "Agnes may become increasingly curious about Will" not in reversed_prompt
    assert "GROWING_CURIOSITY_ABOUT_WILL" not in reversed_prompt

    generic_prompt = build_dual_generation_prompt(
        script="ALICE: Hello.\nBOB: Hello.", character_a="ALICE", character_b="BOB"
    )
    assert "ALICE and BOB are immutable script identities." in generic_prompt
    assert "Concrete INITIAL illustration: `AGNES`" in generic_prompt
    assert "`WILL` with `affect: Nervous-55`, `focus: AGNES`" in generic_prompt


def test_semantic_beat_generation_preserves_raw_and_compiles_chayton_joan_fixture(tmp_path: Path):
    raw = """[INITIAL]
CHAYTON
affect: Watchful-80
focus: JOAN
acting: He begins focused on assessing her response.

JOAN
affect: Nervous-65
focus: CHAYTON
acting: She begins guarded while concealing what she knows.
[BEATS]
E001
actor: JOAN
trigger: w0004
acting: The danger cue makes her briefly check the hidden entrance.
affect: Nervous-75
eye_action: brief_check FARMHOUSE_ENTRANCE
"""

    def runner(**kwargs):
        Path(kwargs["output_text"]).write_text(raw, encoding="utf-8")
        Path(kwargs["output_meta"]).write_text('{"status":"completed"}\n', encoding="utf-8")
        return raw, {"status": "completed"}

    run_dir = tmp_path / "semantic_run"
    plan = generate_dual_performance_plan(
        script="CHAYTON: Have you seen anyone?\nJOAN: No.",
        character_a="CHAYTON", character_b="JOAN", run_id="semantic_run",
        output_dir=run_dir, proposal_runner=runner,
    )
    assert (run_dir / "semantic_beats.txt").read_text(encoding="utf-8") == raw
    assert (run_dir / "semantic_beats.json").is_file()
    assert plan["initial_states"]["JOAN"]["gaze"] == "GAZE-CHAYTON"
    assert plan["tracks"]["JOAN"][0]["changes"] == {
        "affect": "Nervous-75", "gaze": "GLANCE-FARMHOUSE_ENTRANCE"
    }
    assert plan["gaze_target_candidates"] == ["FARMHOUSE_ENTRANCE"]


def test_backend_runner_keeps_single_command_and_builds_dual_command(tmp_path: Path):
    single = prepare_generation_command(
        mode="single", script="AGNES: Hello.", context=None, character_a="AGNES",
        repo_root=tmp_path, backend_python="python.exe", run_id="single",
    )
    assert single.arguments[:2] == ("-m", "expregaze_jali.generate_performance_plan")
    dual = prepare_generation_command(
        mode="dual", script=SCRIPT, context="Garden", character_a="AGNES", character_b="WILL",
        repo_root=tmp_path, backend_python="python.exe", run_id="dual",
    )
    assert dual.arguments[:2] == ("-m", "expregaze_jali.generate_dual_performance_plan")
    assert dual.arguments[dual.arguments.index("--character-a") + 1] == "AGNES"
    assert dual.arguments[dual.arguments.index("--character-b") + 1] == "WILL"


def test_maya_ui_source_routes_dual_generation_to_emotion_only_stage():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    assert 'mode="dual" if dual else "single"' in source
    assert "character_a=character_a" in source and "character_b=character_b if dual else None" in source
    assert "DualPerformanceScoreModel" in source
    assert "self._generate_dual_speaker_emotion()" in source
    assert "apply_dual_speaker_emotion_artifacts" in source
    assert "Dual Animation Not Supported" not in source


def test_maya_plan_loader_accepts_dual_phrase_schema(tmp_path: Path):
    path = tmp_path / "performance_plan.json"
    path.write_text(json.dumps(build_plan()), encoding="utf-8")
    loaded = load_performance_plan(path)
    assert loaded["schema_version"] == "dual_performance_plan_v0"
    assert len(loaded["phrases"]) == 5
