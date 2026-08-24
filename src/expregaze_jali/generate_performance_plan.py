"""HCI production entry point for script-to-Performance-Plan generation.

This path deliberately has no MovieNet, shot-range, full-context, or sequence-
configuration dependencies.  It reuses the established actor prompt, LLM,
annotation parser, and Performance Plan normalizer components.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable

from expregaze_jali.actor_prompt_builder import (
    build_actor_annotation_prompt,
    load_extra_config_texts,
    load_prompt_template,
)
from expregaze_jali.performance_annotation_parser import parse_performance_annotation
from expregaze_jali.performance_plan_normalizer import normalize_performance_plan
from expregaze_jali.run_actor_llm import generate_actor_annotation_artifacts


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_TEMPLATE = REPO_ROOT / "prompts" / "actor_performance_annotation_prompt_v2.md"
DEFAULT_EXTRA_CONFIG_FILES = (
    REPO_ROOT / "configs" / "jali_emotion_options.yaml",
    REPO_ROOT / "configs" / "performance_rules.yaml",
)
DEFAULT_LLM_CONFIG = REPO_ROOT / "configs" / "llm.yaml"
DEFAULT_HCI_RUNS_DIR = REPO_ROOT / "data" / "processed" / "hci_runs"
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class HciRunPaths:
    output_dir: Path
    prompt: Path
    annotation: Path
    response_meta: Path
    performance_plan: Path


AnnotationRunner = Callable[..., tuple[str, dict[str, Any]]]


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
        annotation=run_dir / "performance_annotation.txt",
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
    template = load_prompt_template(prompt_template_path)
    extra_config = load_extra_config_texts(list(extra_config_paths))
    return build_actor_annotation_prompt(
        prompt_template=template,
        context_pack=build_hci_context_pack(
            context=context,
            target_character=target_character,
        ),
        transcript=clean_script,
        extra_config=extra_config,
    )


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
    annotation_runner: AnnotationRunner | None = None,
) -> dict[str, Any]:
    """Generate and persist one canonical Performance Plan from HCI inputs."""
    clean_script = str(script)
    if not clean_script.strip():
        raise ValueError("Script is required.")
    resolved_run_id = validate_run_id(run_id or generate_run_id())
    paths = hci_run_paths(resolved_run_id, output_dir)
    prompt = build_hci_generation_prompt(
        script=clean_script,
        context=context,
        target_character=target_character,
        prompt_template_path=prompt_template_path,
        extra_config_paths=extra_config_paths,
    )

    for path in (paths.prompt, paths.annotation, paths.response_meta, paths.performance_plan):
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"Refusing to overwrite existing file without --overwrite: {path}"
            )

    _write_text(paths.prompt, prompt, overwrite=overwrite)
    print(f"Run ID: {resolved_run_id}", flush=True)
    print(f"Prompt: {paths.prompt}", flush=True)
    print("LLM calls: 1", flush=True)

    runner = annotation_runner or generate_actor_annotation_artifacts
    runner(
        prompt=prompt,
        llm_config_path=llm_config_path,
        prompt_path=paths.prompt,
        output_annotation=paths.annotation,
        output_meta=paths.response_meta,
        overwrite=overwrite,
    )

    parsed = parse_performance_annotation(paths.annotation)
    normalization_context = build_hci_context_pack(
        context=context,
        target_character=target_character,
    )
    # The prompt builder receives the transcript separately, while the existing
    # normalizer uses this field to detect any LLM rewrite of the source script.
    normalization_context["exact_transcript"] = clean_script
    plan = normalize_performance_plan(
        parsed,
        sequence_id=resolved_run_id,
        context_pack=normalization_context,
        target_character=str(target_character).strip() if target_character else None,
    )
    _write_json(paths.performance_plan, plan, overwrite=overwrite)
    print(f"Annotation: {paths.annotation}", flush=True)
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
