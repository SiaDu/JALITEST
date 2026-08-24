from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from expregaze_jali.generate_performance_plan import (
    build_hci_context_pack,
    build_hci_generation_prompt,
    generate_performance_plan,
    hci_run_paths,
)


ROOT = Path(__file__).resolve().parents[1]
MAYA_TOOLS = ROOT / "tools" / "maya"


def _proposal_runner(span: str, *, gaze: str = "NONE"):
    def run(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        proposal = (
            "[ANALYZE]\n\n"
            "scene_constraints:\nUser-authored scene.\n\n"
            "[PERFORMANCE]\n\n"
            "S01\n"
            f"span: {span}\n"
            "intent: deliver line\n"
            "affect: Friendly-50\n"
            "heart: NONE\n"
            f"gaze: {gaze}\n"
            "head: NONE\n"
            "lid: NONE\n"
            "blink: NONE\n"
            "blink_suppression: NONE\n\n"
            "[REASONS]\n\n"
            "S01.intent: gives the line one coherent acting intention\n"
            "S01.affect: friendly delivery\n"
            "S01.head: deliberate head stillness\n"
        )
        meta = {"response_id": "test-response", "status": "completed"}
        Path(kwargs["output_text"]).write_text(proposal, encoding="utf-8")
        Path(kwargs["output_meta"]).write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
        return proposal, meta

    return run


def test_hci_prompt_accepts_script_only_and_empty_context():
    script = "AUNT EM: I cannot allow that."
    for context in (None, "", "   "):
        prompt = build_hci_generation_prompt(
            script=script,
            context=context,
            target_character="AUNT_EM",
        )
        assert script in prompt
        assert "AUNT_EM" in prompt
        assert "user_context" not in prompt


def test_hci_prompt_contains_optional_free_text_context():
    context = "Aunt Em is normally restrained but is finally losing her patience."
    prompt = build_hci_generation_prompt(
        script="AUNT EM: I cannot allow that.",
        context=context,
        target_character="AUNT_EM",
    )
    assert context in prompt
    assert build_hci_context_pack(context=context, target_character="AUNT_EM") == {
        "user_context": context,
        "target_character": "AUNT_EM",
    }


def test_hci_prompt_contains_immutable_and_anchored_script_and_proposal_contract():
    script = "AGNES: Good day.\nWILL: Yes, I, uh... I suppose."
    prompt = build_hci_generation_prompt(
        script=script, context=None, target_character="WILL"
    )
    assert "[IMMUTABLE SCRIPT]" in prompt
    assert "[ANCHORED SCRIPT]" in prompt
    assert "T02 WILL:" in prompt
    assert "[w0003 Yes,]" in prompt
    assert '"A": "WILL"' in prompt and '"B": "AGNES"' in prompt
    assert "span: w0001-w0003" in prompt
    assert "Never copy dialogue into the response" in prompt


def test_hci_prompt_has_no_dataset_input_requirements():
    prompt = build_hci_generation_prompt(
        script="PROFESSOR: Sit down.",
        context=None,
        target_character="PROFESSOR",
    ).lower()
    for forbidden in (
        "movie_id",
        "movie_name",
        "shot_range",
        "start_shot_idx",
        "end_shot_idx",
        "local_window",
        "context_window",
        "full_context",
        "sequence_config",
    ):
        assert forbidden not in prompt


def test_empty_script_is_rejected_before_generation(tmp_path: Path):
    with pytest.raises(ValueError, match="Script is required"):
        generate_performance_plan(
            script="  ",
            context=None,
            target_character="ACTOR",
            run_id="run_empty",
            output_dir=tmp_path,
            proposal_runner=_proposal_runner("w0001-w0001"),
        )


def test_hci_generation_writes_canonical_artifacts_and_target_character(tmp_path: Path):
    script = "AUNT EM: Almira Gulch, you have no power over us!"
    run_dir = tmp_path / "run_test"
    plan = generate_performance_plan(
        script=script,
        context="Aunt Em is normally restrained.",
        target_character="AUNT_EM",
        run_id="run_test",
        output_dir=run_dir,
        overwrite=True,
        proposal_runner=_proposal_runner("w0001-w0008"),
    )
    paths = hci_run_paths("run_test", run_dir)
    assert paths.prompt.exists()
    assert paths.anchored_script.exists()
    assert paths.anchor_map.exists()
    assert paths.proposal.exists()
    assert paths.response_meta.exists()
    assert paths.performance_plan.exists()
    saved = json.loads(paths.performance_plan.read_text(encoding="utf-8"))
    assert saved == plan
    assert plan["sequence_id"] == "run_test"
    assert plan["target_character"] == "AUNT_EM"
    assert plan["events"][0]["span"]["text"] == "Almira Gulch, you have no power over us!"
    assert plan["acting_interpretation"].startswith("scene_constraints:")

    import sys

    if str(MAYA_TOOLS) not in sys.path:
        sys.path.insert(0, str(MAYA_TOOLS))
    from performance_score_model import PerformanceScoreModel

    model = PerformanceScoreModel(plan)
    assert model.phrases
    assert model.validate(model.score_text).valid


def test_hci_generation_never_uses_llm_transcript_text(tmp_path: Path):
    plan = generate_performance_plan(
        script="ACTOR: Original line.",
        context=None,
        target_character="ACTOR",
        run_id="run_mismatch",
        output_dir=tmp_path,
        overwrite=True,
        proposal_runner=_proposal_runner("w0001-w0002"),
    )

    assert plan["events"][0]["span"]["text"] == "Original line."
    assert plan["diagnostics"]["errors"] == []
    assert "Original line." not in hci_run_paths("run_mismatch", tmp_path).proposal.read_text(encoding="utf-8")


def test_hci_generation_accepts_object_gaze_without_maya_mapping(tmp_path: Path):
    plan = generate_performance_plan(
        script="AGNES: Look there.",
        target_character="AGNES",
        run_id="run_object_gaze",
        output_dir=tmp_path,
        overwrite=True,
        proposal_runner=_proposal_runner("w0001-w0002", gaze="GAZE-OBJECT_HAWK"),
    )
    assert plan["events"][0]["gaze"][0]["value"] == "GAZE-OBJECT_HAWK"
