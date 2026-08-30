"""One-call shared dual-character semantic Performance Plan generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable

from expregaze_jali.actor_prompt_builder import load_prompt_template
from expregaze_jali.dual_performance_plan_v2 import build_dual_performance_plan_v2
from expregaze_jali.dual_sparse_performance_proposal_parser import BLINK_VALUES, DIRECTION_TARGETS, HEAD_VALUES
from expregaze_jali.dual_semantic_beat_parser import parse_dual_semantic_beats
from expregaze_jali.compile_dual_semantic_beats import compile_dual_semantic_beats, render_compiled_dual_performance_proposal
from expregaze_jali.generate_performance_plan import (
    DEFAULT_EXTRA_CONFIG_FILES,
    DEFAULT_HCI_RUNS_DIR,
    DEFAULT_LLM_CONFIG,
    generate_run_id,
    hci_run_paths,
    validate_run_id,
)
from expregaze_jali.performance_proposal_parser import load_semantic_vocabulary
from expregaze_jali.run_actor_llm import generate_text_artifacts
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel, build_conversation_anchor_model


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_TEMPLATE = REPO_ROOT / "prompts" / "actor_dual_semantic_beat_prompt_v1.md"
ProposalRunner = Callable[..., tuple[str, dict[str, Any]]]


def _semantic_reference(config_paths: Iterable[str | Path]) -> str:
    paths = list(config_paths)
    if not paths:
        raise ValueError("A JALI emotion vocabulary config is required.")
    vocabulary = load_semantic_vocabulary(paths[0])
    return "\n".join((
        "VISIBLE AFFECT — CLOSED VOCABULARY: " + " | ".join(vocabulary.affect_states.values()) + " | MASK-NONE",
        "Any other value is invalid in this executable field.",
        "ACTING RATIONALE: Open natural language is allowed only in reason fields.",
        "Head values: " + ", ".join(sorted(HEAD_VALUES)),
        "Blink values: " + ", ".join(sorted(BLINK_VALUES)),
        "Directional gaze targets: " + ", ".join(sorted(DIRECTION_TARGETS)),
    ))
def _render_dual_prompt(
    *, anchor_model: ConversationAnchorModel, context: str | None,
    prompt_template_path: str | Path, extra_config_paths: Iterable[str | Path],
) -> str:
    prompt = load_prompt_template(prompt_template_path)
    character_a, character_b = anchor_model.aliases["A"], anchor_model.aliases["B"]
    identity_contract = "\n".join(("IDENTITY CONTRACT", f"{character_a} and {character_b} are immutable script identities.", "Never reinterpret, swap, or infer identity from dialogue order, personality, speaker order, examples, or narrative role."))
    replacements = {
        "{{character_a}}": character_a,
        "{{character_b}}": character_b,
        "{{alias_map}}": json.dumps(anchor_model.aliases, ensure_ascii=False, sort_keys=True),
        "{{identity_contract}}": identity_contract,
        "{{context}}": str(context or "").strip() or "NONE",
        "{{immutable_script}}": anchor_model.script,
        "{{anchored_script}}": anchor_model.anchored_dialogue_with_speaker_metadata().rstrip("\n"),
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
    semantic_beats = run_dir / "semantic_beats.txt"
    semantic_beats_json = run_dir / "semantic_beats.json"
    artifacts = (
        run_dir / "input_script.txt", run_dir / "input_context.txt", paths.prompt,
        paths.anchored_script, paths.anchor_map, semantic_beats, semantic_beats_json, paths.proposal, paths.response_meta,
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
    semantic_beat_text, _meta = runner(
        prompt=prompt, llm_config_path=llm_config_path, prompt_path=paths.prompt,
        output_text=semantic_beats, output_meta=paths.response_meta,
        required_sections=("[INITIAL]", "[BEATS]"),
        artifact_name="semantic beats", overwrite=overwrite,
    )
    vocabulary = load_semantic_vocabulary(extras[0])
    semantic_ir = parse_dual_semantic_beats(semantic_beat_text, vocabulary=vocabulary, anchor_model=model)
    semantic_beats_json.write_text(json.dumps(semantic_ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    proposal = compile_dual_semantic_beats(semantic_ir, anchor_model=model)
    paths.proposal.write_text(render_compiled_dual_performance_proposal(proposal, characters=(character_a, character_b)), encoding="utf-8")
    plan = build_dual_performance_plan_v2(
        proposal, anchor_model=model, sequence_id=resolved_run_id,
        proposal_path=str(paths.proposal),
    )
    _write_text(paths.performance_plan, json.dumps(plan, ensure_ascii=False, indent=2) + "\n", overwrite)
    print(f"Events: {sum(len(track) for track in plan['tracks'].values())}", flush=True)
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
