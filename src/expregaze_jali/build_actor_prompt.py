from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from expregaze_jali.actor_context_builder import (
    build_actor_context_pack,
    find_candidate,
    load_full_context_window,
)
from expregaze_jali.actor_prompt_builder import (
    build_actor_annotation_prompt,
    load_prompt_template,
)


DEFAULT_SEQUENCE_ID = "Jali_proto_candidate_001_ProfessorCrystal"
DEFAULT_CANDIDATES = Path("data/processed/candidate_sequences/Jali_proto_candidate_sequences.jsonl")
DEFAULT_FULL_CONTEXT = Path("data/processed/full_context/tt0032138__full_context.csv")
DEFAULT_TEMPLATE = Path("prompts/actor_performance_annotation_prompt_v2.md")
DEFAULT_PATHS_CONFIG = Path("configs/path_local.yaml")
DEFAULT_OUTPUT_DIR = Path("data/processed/gaze_script/llm_process")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Step 00: build compact context pack and actor-style LLM prompt. Does not call the LLM."
    )
    parser.add_argument("--sequence-id", default=DEFAULT_SEQUENCE_ID)
    parser.add_argument("--candidate-jsonl", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--full-context-csv", type=Path, default=DEFAULT_FULL_CONTEXT)
    parser.add_argument("--full-context-window", type=int, default=2)
    parser.add_argument("--no-full-context", action="store_true")
    parser.add_argument("--prompt-template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--paths-config", type=Path, default=DEFAULT_PATHS_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-prompt", type=Path, default=None)
    parser.add_argument("--output-context-pack", type=Path, default=None)
    parser.add_argument(
        "--exact-transcript-file",
        type=Path,
        default=None,
        help=(
            "Optional manually edited exact transcript. If omitted, Step 00 tries "
            "paths_config.jali.project_root / paths_config.jali.input_dir / {sequence_id}.txt."
        ),
    )
    parser.add_argument(
        "--no-auto-exact-transcript-file",
        action="store_true",
        help=(
            "Do not auto-load {sequence_id}.txt from paths_config.jali.project_root/input_dir; "
            "fall back to candidate subtitle_text."
        ),
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _write_text(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing file without --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return data


def _resolve_auto_exact_transcript_file(paths_config: Path, sequence_id: str) -> Path | None:
    """Resolve the default JALI transcript path from configs/path_local.yaml.

    Primary convention:
        jali.project_root / jali.input_dir / {sequence_id}.txt

    Fallback convention:
        jali.project_root / jali.transcript_file
    """
    config = _read_yaml(paths_config)
    jali = config.get("jali") or {}
    if not isinstance(jali, dict):
        return None

    project_root_raw = jali.get("project_root")
    input_dir_raw = jali.get("input_dir")
    transcript_file_raw = jali.get("transcript_file")

    candidates: list[Path] = []
    project_root = Path(str(project_root_raw)) if project_root_raw else None

    if project_root is not None and input_dir_raw:
        candidates.append(project_root / str(input_dir_raw) / f"{sequence_id}.txt")

    if transcript_file_raw:
        transcript_file = Path(str(transcript_file_raw))
        if project_root is not None and not transcript_file.is_absolute():
            candidates.append(project_root / transcript_file)
        candidates.append(transcript_file)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0] if candidates else None


def _load_exact_transcript(args: argparse.Namespace) -> tuple[str | None, str]:
    if args.exact_transcript_file is not None:
        path = args.exact_transcript_file
        return path.read_text(encoding="utf-8"), str(path)

    if args.no_auto_exact_transcript_file:
        return None, "candidate subtitle_text (--no-auto-exact-transcript-file)"

    path = _resolve_auto_exact_transcript_file(args.paths_config, args.sequence_id)
    if path is None:
        return None, "candidate subtitle_text (no transcript path in paths config)"

    if path.exists():
        return path.read_text(encoding="utf-8"), str(path)

    print(f"WARNING: auto exact transcript file not found: {path}")
    return None, "candidate subtitle_text (auto transcript file missing)"


def main() -> None:
    args = parse_args()

    candidate = find_candidate(args.candidate_jsonl, args.sequence_id)
    full_rows = []
    if not args.no_full_context:
        full_rows = load_full_context_window(
            args.full_context_csv,
            movie_id=str(candidate["movie_id"]),
            start_shot_idx=int(candidate["start_shot_idx"]),
            end_shot_idx=int(candidate["end_shot_idx"]),
            window=args.full_context_window,
        )

    exact_transcript, exact_transcript_source = _load_exact_transcript(args)

    context_pack = build_actor_context_pack(candidate, full_rows, exact_transcript=exact_transcript)
    template = load_prompt_template(args.prompt_template)
    prompt = build_actor_annotation_prompt(
        prompt_template=template,
        context_pack=context_pack,
    )

    output_prompt = args.output_prompt or args.output_dir / f"{args.sequence_id}__actor_prompt.txt"
    output_context = args.output_context_pack or args.output_dir / f"{args.sequence_id}__context_pack.json"

    _write_text(output_prompt, prompt, overwrite=args.overwrite)
    _write_text(output_context, json.dumps(context_pack, ensure_ascii=False, indent=2), overwrite=args.overwrite)

    exact_preview = context_pack.get("exact_transcript", "").replace("\n", " ")[:120]
    print(f"Context pack: {output_context}")
    print(f"Prompt: {output_prompt}")
    print("Annotation mode: actor-style full tag set")
    print(f"Exact transcript source: {exact_transcript_source}")
    print(f"Transcript chars: {len(context_pack.get('exact_transcript', ''))}")
    print(f"Exact transcript preview: {exact_preview}")
    print(f"Full-context rows: {len(full_rows)}")
    print("LLM calls: 0")


if __name__ == "__main__":
    main()
