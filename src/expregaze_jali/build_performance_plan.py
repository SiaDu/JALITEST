from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from expregaze_jali.performance_annotation_parser import parse_performance_annotation
from expregaze_jali.performance_plan_normalizer import normalize_performance_plan

DEFAULT_LLM_PROCESS_DIR = Path("data/processed/gaze_script/llm_process")
DEFAULT_OUTPUT_DIR = Path("data/processed/performance_plan")


def _read_context(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Context pack must contain a JSON object: {path}")
    return value


def build_performance_plan(
    *,
    sequence_id: str,
    annotation_path: Path,
    context_path: Path,
    output_path: Path,
    target_character: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Refusing to overwrite existing file without --overwrite: {output_path}"
        )

    parsed = parse_performance_annotation(annotation_path)
    context_pack = _read_context(context_path)
    plan = normalize_performance_plan(
        parsed,
        sequence_id=sequence_id,
        context_pack=context_pack,
        target_character=target_character,
    )
    if context_pack is None:
        plan["diagnostics"]["warnings"].append(f"context pack is missing: {context_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a semantic Performance Plan from actor-style annotation. No LLM or timing calls."
    )
    parser.add_argument("--sequence-id", required=True)
    parser.add_argument("--annotation", type=Path, default=None)
    parser.add_argument("--context", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--target-character", default=None)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    annotation_path = (
        args.annotation
        or DEFAULT_LLM_PROCESS_DIR / f"{args.sequence_id}__performance_annotation.txt"
    )
    context_path = args.context or DEFAULT_LLM_PROCESS_DIR / f"{args.sequence_id}__context_pack.json"
    output_path = args.output or DEFAULT_OUTPUT_DIR / f"{args.sequence_id}__performance_plan.json"
    plan = build_performance_plan(
        sequence_id=args.sequence_id,
        annotation_path=annotation_path,
        context_path=context_path,
        output_path=output_path,
        target_character=args.target_character,
        overwrite=args.overwrite,
    )

    print(f"Annotation: {annotation_path}")
    print(f"Context: {context_path if context_path.exists() else 'missing'}")
    print(f"Events: {len(plan['events'])}")
    print(f"Errors: {len(plan['diagnostics']['errors'])}")
    print(f"Warnings: {len(plan['diagnostics']['warnings'])}")
    print(f"Performance Plan: {output_path}")


if __name__ == "__main__":
    main()
