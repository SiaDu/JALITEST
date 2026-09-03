"""HCI production entry point for script-to-Performance-Plan generation.

This path deliberately has no MovieNet, shot-range, full-context, or sequence-
configuration dependencies. The LLM proposes semantics against deterministic
word anchors; code owns the immutable transcript, offsets, and canonical tags.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable

from expregaze_jali.prompt_templates import load_prompt_template
from expregaze_jali.performance_plan_from_proposal import build_performance_plan_from_proposal
from expregaze_jali.performance_proposal_parser import (
    BLINK_VALUES,
    DIRECTION_TARGETS,
    HEAD_VALUES,
    LID_VALUES,
    SUPPRESSION_VALUES,
    DEFAULT_SEMANTIC_VOCABULARY_PATH,
    load_semantic_vocabulary,
    parse_performance_proposal,
)
from expregaze_jali.run_actor_llm import generate_text_artifacts
from expregaze_jali.transcript_anchor_model import (
    TranscriptAnchorModel,
    build_transcript_anchor_model,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_TEMPLATE = REPO_ROOT / "prompts" / "single_performance_plan_prompt.md"
DEFAULT_EXTRA_CONFIG_FILES = (
    DEFAULT_SEMANTIC_VOCABULARY_PATH,
    REPO_ROOT / "configs" / "performance_rules.yaml",
)
DEFAULT_LLM_CONFIG = REPO_ROOT / "configs" / "llm.yaml"
DEFAULT_HCI_RUNS_DIR = REPO_ROOT / "data" / "processed" / "hci_runs"
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class HciRunPaths:
    output_dir: Path
    prompt: Path
    anchored_script: Path
    anchor_map: Path
    proposal: Path
    response_meta: Path
    performance_plan: Path


ProposalRunner = Callable[..., tuple[str, dict[str, Any]]]


def generate_run_id(now: datetime | None = None) -> str:
    instant = now or datetime.now(timezone.utc)
    return instant.strftime("run_%Y%m%d_%H%M%S_%f")


def validate_run_id(run_id: str) -> str:
    clean = str(run_id).strip()
    if not clean or not _SAFE_RUN_ID.fullmatch(clean):
        raise ValueError("run_id must contain only letters, digits, dot, underscore, or hyphen.")
    return clean


def hci_run_paths(run_id: str, output_dir: str | Path | None = None) -> HciRunPaths:
    clean_run_id = validate_run_id(run_id)
    run_dir = Path(output_dir) if output_dir is not None else DEFAULT_HCI_RUNS_DIR / clean_run_id
    return HciRunPaths(
        output_dir=run_dir,
        prompt=run_dir / "actor_prompt.txt",
        anchored_script=run_dir / "anchored_script.txt",
        anchor_map=run_dir / "anchor_map.json",
        proposal=run_dir / "performance_proposal.txt",
        response_meta=run_dir / "llm_response_meta.json",
        performance_plan=run_dir / "performance_plan.json",
    )


def build_hci_context_pack(
    *, context: str | None, target_character: str | None
) -> dict[str, str]:
    context_pack: dict[str, str] = {}
    clean_context = str(context or "").strip()
    clean_character = str(target_character or "").strip()
    if clean_context:
        context_pack["user_context"] = clean_context
    if clean_character:
        context_pack["target_character"] = clean_character
    return context_pack


def build_hci_generation_prompt(
    *,
    script: str,
    context: str | None = None,
    target_character: str | None = None,
    prompt_template_path: str | Path = DEFAULT_PROMPT_TEMPLATE,
    extra_config_paths: Iterable[str | Path] = DEFAULT_EXTRA_CONFIG_FILES,
) -> str:
    clean_script = str(script)
    if not clean_script.strip():
        raise ValueError("Script is required.")
    clean_character = str(target_character or "").strip()
    if not clean_character:
        raise ValueError("target_character is required.")
    anchor_model = build_transcript_anchor_model(clean_script, target_character=clean_character)
    return _render_hci_prompt(
        anchor_model=anchor_model,
        context=context,
        prompt_template_path=prompt_template_path,
        extra_config_paths=extra_config_paths,
    )


def _render_hci_prompt(
    *,
    anchor_model: TranscriptAnchorModel,
    context: str | None,
    prompt_template_path: str | Path,
    extra_config_paths: Iterable[str | Path],
) -> str:
    template = load_prompt_template(prompt_template_path)
    config_paths = list(extra_config_paths)
    if not config_paths:
        raise ValueError("A JALI emotion vocabulary config is required.")
    vocabulary = load_semantic_vocabulary(config_paths[0])
    semantic_reference = "\n".join(
        (
            "VISIBLE AFFECT — CLOSED VOCABULARY: "
            + " | ".join(vocabulary.affect_states.values()) + " | NONE",
            "HEART — CLOSED VOCABULARY: "
            + " | ".join(vocabulary.heart_states.values()) + " | NONE",
            "Any other value is invalid in these two executable fields.",
            "ACTING LANGUAGE: Open vocabulary in ANALYZE / intent / reasons.",
            "Head values: " + ", ".join(HEAD_VALUES),
            "Lid values: " + ", ".join(str(value) for value in sorted(LID_VALUES)),
            "Blink values: " + ", ".join(sorted(BLINK_VALUES)),
            "Blink suppression: " + ", ".join(sorted(SUPPRESSION_VALUES)),
            "Direction targets: " + ", ".join(sorted(DIRECTION_TARGETS)),
        )
    )
    replacements = {
        "{{target_character}}": anchor_model.target_character,
        "{{alias_map}}": json.dumps(anchor_model.aliases, ensure_ascii=False, sort_keys=True),
        "{{alias_guidance}}": (
            "Character aliases available in this run: "
            + ", ".join(f"{alias} = {name}" for alias, name in anchor_model.aliases.items())
            + (". Use only these aliases for known dialogue characters."
               if "B" in anchor_model.aliases
               else ". Only A is currently defined by the script. Do not invent B as a known dialogue character.")
        ),
        "{{context}}": str(context or "").strip() or "NONE",
        "{{immutable_script}}": anchor_model.script,
        "{{anchored_script}}": anchor_model.anchored_script().rstrip("\n"),
        "{{semantic_reference}}": semantic_reference,
    }
    prompt = template
    for marker, value in replacements.items():
        prompt = prompt.replace(marker, value)
    unresolved = re.findall(r"{{[^{}]+}}", prompt)
    if unresolved:
        raise ValueError(f"Unresolved HCI prompt placeholders: {unresolved}")
    return prompt


def _write_text(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing file without --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, value: dict[str, Any], *, overwrite: bool) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", overwrite=overwrite)


def generate_performance_plan(
    *,
    script: str,
    context: str | None = None,
    target_character: str | None = None,
    run_id: str | None = None,
    output_dir: str | Path | None = None,
    prompt_template_path: str | Path = DEFAULT_PROMPT_TEMPLATE,
    extra_config_paths: Iterable[str | Path] = DEFAULT_EXTRA_CONFIG_FILES,
    llm_config_path: str | Path = DEFAULT_LLM_CONFIG,
    overwrite: bool = False,
    proposal_runner: ProposalRunner | None = None,
) -> dict[str, Any]:
    """Generate and persist one canonical Performance Plan from HCI inputs."""
    clean_script = str(script)
    if not clean_script.strip():
        raise ValueError("Script is required.")
    clean_character = str(target_character or "").strip()
    if not clean_character:
        raise ValueError("target_character is required.")
    resolved_run_id = validate_run_id(run_id or generate_run_id())
    paths = hci_run_paths(resolved_run_id, output_dir)
    resolved_extra_config_paths = tuple(extra_config_paths)
    anchor_model = build_transcript_anchor_model(
        clean_script, target_character=clean_character
    )
    prompt = _render_hci_prompt(
        anchor_model=anchor_model,
        context=context,
        prompt_template_path=prompt_template_path,
        extra_config_paths=resolved_extra_config_paths,
    )

    for path in (
        paths.prompt, paths.anchored_script, paths.anchor_map, paths.proposal,
        paths.response_meta, paths.performance_plan,
    ):
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"Refusing to overwrite existing file without --overwrite: {path}"
            )

    _write_text(paths.prompt, prompt, overwrite=overwrite)
    _write_text(paths.anchored_script, anchor_model.anchored_script(), overwrite=overwrite)
    _write_json(paths.anchor_map, anchor_model.anchor_map(), overwrite=overwrite)
    print(f"Run ID: {resolved_run_id}", flush=True)
    print(f"Prompt: {paths.prompt}", flush=True)
    print(f"Anchored Script: {paths.anchored_script}", flush=True)
    print(f"Anchor Map: {paths.anchor_map}", flush=True)
    print("LLM calls: 1", flush=True)

    runner = proposal_runner or generate_text_artifacts
    proposal_text, _meta = runner(
        prompt=prompt,
        llm_config_path=llm_config_path,
        prompt_path=paths.prompt,
        output_text=paths.proposal,
        output_meta=paths.response_meta,
        required_sections=("[ANALYZE]", "[PERFORMANCE]", "[REASONS]"),
        artifact_name="proposal",
        overwrite=overwrite,
    )

    config_paths = list(resolved_extra_config_paths)
    vocabulary = load_semantic_vocabulary(config_paths[0])
    proposal = parse_performance_proposal(proposal_text, vocabulary=vocabulary)
    plan = build_performance_plan_from_proposal(
        proposal,
        anchor_model=anchor_model,
        sequence_id=resolved_run_id,
        proposal_path=str(paths.proposal),
    )
    _write_json(paths.performance_plan, plan, overwrite=overwrite)
    print(f"Proposal: {paths.proposal}", flush=True)
    print(f"LLM meta: {paths.response_meta}", flush=True)
    print(f"Events: {len(plan.get('events', []))}", flush=True)
    print(f"Performance Plan: {paths.performance_plan}", flush=True)
    return plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a canonical Performance Plan from an HCI script and optional context."
    )
    parser.add_argument("--script-file", type=Path, required=True)
    parser.add_argument("--context-file", type=Path, default=None)
    parser.add_argument("--target-character", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--prompt-template", type=Path, default=DEFAULT_PROMPT_TEMPLATE)
    parser.add_argument("--extra-config-file", action="append", type=Path, default=None)
    parser.add_argument("--llm-config", type=Path, default=DEFAULT_LLM_CONFIG)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    script = args.script_file.read_text(encoding="utf-8")
    context = (
        args.context_file.read_text(encoding="utf-8")
        if args.context_file is not None
        else None
    )
    resolved_run_id = validate_run_id(args.run_id or generate_run_id())
    paths = hci_run_paths(resolved_run_id, args.output_dir)
    generate_performance_plan(
        script=script,
        context=context,
        target_character=args.target_character,
        run_id=resolved_run_id,
        output_dir=paths.output_dir,
        prompt_template_path=args.prompt_template,
        extra_config_paths=args.extra_config_file or DEFAULT_EXTRA_CONFIG_FILES,
        llm_config_path=args.llm_config,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
