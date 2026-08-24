"""One-call shared dual-character semantic Performance Plan generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable

from expregaze_jali.actor_prompt_builder import load_prompt_template
from expregaze_jali.dual_performance_plan_from_proposal import build_dual_performance_plan_from_proposal
from expregaze_jali.dual_performance_proposal_parser import parse_dual_performance_proposal
from expregaze_jali.generate_performance_plan import (
    DEFAULT_EXTRA_CONFIG_FILES,
    DEFAULT_HCI_RUNS_DIR,
    DEFAULT_LLM_CONFIG,
    generate_run_id,
    hci_run_paths,
    validate_run_id,
)
from expregaze_jali.performance_proposal_parser import (
    BLINK_VALUES, DIRECTION_TARGETS, HEAD_VALUES, LID_VALUES, SUPPRESSION_VALUES,
    load_semantic_vocabulary,
)
from expregaze_jali.run_actor_llm import generate_text_artifacts
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel, build_conversation_anchor_model


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_TEMPLATE = REPO_ROOT / "prompts" / "actor_dual_performance_proposal_prompt_v1.md"
ProposalRunner = Callable[..., tuple[str, dict[str, Any]]]


def _semantic_reference(config_paths: Iterable[str | Path]) -> str:
    paths = list(config_paths)
    if not paths:
        raise ValueError("A JALI emotion vocabulary config is required.")
    vocabulary = load_semantic_vocabulary(paths[0])
    return "\n".join((
        "Affect states: " + ", ".join(vocabulary.affect_states.values()),
        "Heart states: " + ", ".join(vocabulary.heart_states.values()),
        "Inactive affect or heart channel: NONE",
        "Head values: " + ", ".join(HEAD_VALUES),
        "Lid values: " + ", ".join(str(value) for value in sorted(LID_VALUES)),
        "Blink values: " + ", ".join(sorted(BLINK_VALUES)),
        "Blink suppression: " + ", ".join(sorted(SUPPRESSION_VALUES)),
        "Direction targets: " + ", ".join(sorted(DIRECTION_TARGETS)),
    ))


def _render_dual_prompt(
    *, anchor_model: ConversationAnchorModel, context: str | None,
    prompt_template_path: str | Path, extra_config_paths: Iterable[str | Path],
) -> str:
    prompt = load_prompt_template(prompt_template_path)
    replacements = {
        "{{character_a}}": anchor_model.aliases["A"],
        "{{character_b}}": anchor_model.aliases["B"],
        "{{alias_map}}": json.dumps(anchor_model.aliases, ensure_ascii=False, sort_keys=True),
        "{{context}}": str(context or "").strip() or "NONE",
        "{{immutable_script}}": anchor_model.script,
        "{{anchored_script}}": anchor_model.anchored_script().rstrip("\n"),
        "{{semantic_reference}}": _semantic_reference(extra_config_paths),
    }
    for marker, value in replacements.items():
        prompt = prompt.replace(marker, value)
    unresolved = re.findall(r"{{[^{}]+}}", prompt)
    if unresolved:
        raise ValueError(f"Unresolved dual prompt placeholders: {unresolved}")
    return prompt


def build_dual_generation_prompt(
    *, script: str, character_a: str, character_b: str, context: str | None = None,
    prompt_template_path: str | Path = DEFAULT_PROMPT_TEMPLATE,
    extra_config_paths: Iterable[str | Path] = DEFAULT_EXTRA_CONFIG_FILES,
) -> str:
    model = build_conversation_anchor_model(
        script, character_a=character_a, character_b=character_b
    )
    return _render_dual_prompt(
        anchor_model=model, context=context, prompt_template_path=prompt_template_path,
        extra_config_paths=extra_config_paths,
    )


def _write_text(path: Path, text: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing file without --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def generate_dual_performance_plan(
    *, script: str, character_a: str, character_b: str, context: str | None = None,
    run_id: str | None = None, output_dir: str | Path | None = None,
    prompt_template_path: str | Path = DEFAULT_PROMPT_TEMPLATE,
    extra_config_paths: Iterable[str | Path] = DEFAULT_EXTRA_CONFIG_FILES,
    llm_config_path: str | Path = DEFAULT_LLM_CONFIG, overwrite: bool = False,
    proposal_runner: ProposalRunner | None = None,
) -> dict[str, Any]:
    resolved_run_id = validate_run_id(run_id or generate_run_id())
    run_dir = Path(output_dir) if output_dir is not None else DEFAULT_HCI_RUNS_DIR / resolved_run_id
    paths = hci_run_paths(resolved_run_id, run_dir)
    extras = tuple(extra_config_paths)
    model = build_conversation_anchor_model(
        script, character_a=character_a, character_b=character_b
    )
    prompt = _render_dual_prompt(
        anchor_model=model, context=context, prompt_template_path=prompt_template_path,
        extra_config_paths=extras,
    )
    artifacts = (
        run_dir / "input_script.txt", run_dir / "input_context.txt", paths.prompt,
        paths.anchored_script, paths.anchor_map, paths.proposal, paths.response_meta,
        paths.performance_plan,
    )
    for path in artifacts:
        if path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing file without --overwrite: {path}")
    _write_text(run_dir / "input_script.txt", model.script, overwrite)
    _write_text(run_dir / "input_context.txt", str(context or ""), overwrite)
    _write_text(paths.prompt, prompt, overwrite)
    _write_text(paths.anchored_script, model.anchored_script(), overwrite)
    _write_text(paths.anchor_map, json.dumps(model.anchor_map(), ensure_ascii=False, indent=2) + "\n", overwrite)
    print(f"Run ID: {resolved_run_id}", flush=True)
    print("LLM calls: 1", flush=True)
    runner = proposal_runner or generate_text_artifacts
    proposal_text, _meta = runner(
        prompt=prompt, llm_config_path=llm_config_path, prompt_path=paths.prompt,
        output_text=paths.proposal, output_meta=paths.response_meta,
        required_sections=("[ANALYZE]", "[PERFORMANCE]", "[REASONS]"),
        artifact_name="proposal", overwrite=overwrite,
    )
    vocabulary = load_semantic_vocabulary(extras[0])
    proposal = parse_dual_performance_proposal(proposal_text, vocabulary=vocabulary)
    plan = build_dual_performance_plan_from_proposal(
        proposal, anchor_model=model, sequence_id=resolved_run_id,
        proposal_path=str(paths.proposal),
    )
    _write_text(paths.performance_plan, json.dumps(plan, ensure_ascii=False, indent=2) + "\n", overwrite)
    print(f"Phrases: {len(plan['phrases'])}", flush=True)
    print(f"Performance Plan: {paths.performance_plan}", flush=True)
    return plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate one shared dual-character semantic plan.")
    parser.add_argument("--script-file", type=Path, required=True)
    parser.add_argument("--context-file", type=Path)
    parser.add_argument("--character-a", required=True)
    parser.add_argument("--character-b", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--prompt-template", type=Path, default=DEFAULT_PROMPT_TEMPLATE)
    parser.add_argument("--extra-config-file", action="append", type=Path)
    parser.add_argument("--llm-config", type=Path, default=DEFAULT_LLM_CONFIG)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generate_dual_performance_plan(
        script=args.script_file.read_text(encoding="utf-8"),
        context=args.context_file.read_text(encoding="utf-8") if args.context_file else None,
        character_a=args.character_a, character_b=args.character_b,
        run_id=args.run_id, output_dir=args.output_dir,
        prompt_template_path=args.prompt_template,
        extra_config_paths=args.extra_config_file or DEFAULT_EXTRA_CONFIG_FILES,
        llm_config_path=args.llm_config, overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
