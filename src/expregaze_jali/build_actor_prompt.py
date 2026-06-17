from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from expregaze_jali.actor_context_builder import (
    build_context_pack_from_shot_range,
    load_full_context_records,
)
from expregaze_jali.actor_prompt_builder import (
    build_actor_annotation_prompt,
    load_extra_config_texts,
    load_prompt_template,
)
from expregaze_jali.config_utils import (
    DEFAULT_PROJECT_CONFIG,
    DEFAULT_SEQUENCE_CONFIG,
    clip_name_from_config,
    full_context_path,
    llm_process_dir,
    local_window_from_config,
    movie_id_from_config,
    movie_name_from_config,
    prompt_extra_config_paths,
    prompt_template_path,
    read_yaml,
    repo_root_from_project_config,
    sequence_id_from_config,
    sequence_section,
    shot_range_from_config,
    transcript_path,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Step 00: build context_pack.json and actor prompt from full_context + shot_range. "
            "Does not call the LLM and does not read candidate_sequence files."
        )
    )
    parser.add_argument("--project-config", type=Path, default=DEFAULT_PROJECT_CONFIG)
    parser.add_argument("--sequence-config", type=Path, default=DEFAULT_SEQUENCE_CONFIG)

    # Backward-compatible alias for older command lines.
    parser.add_argument("--paths-config", dest="sequence_config_alias", type=Path, default=None, help=argparse.SUPPRESS)

    parser.add_argument("--sequence-id", default=None)
    parser.add_argument("--movie-id", default=None)
    parser.add_argument("--movie-name", default=None)
    parser.add_argument("--start-shot-idx", type=int, default=None)
    parser.add_argument("--end-shot-idx", type=int, default=None)
    parser.add_argument("--context-window", "--full-context-window", dest="context_window", type=int, default=None)
    parser.add_argument("--full-context-file", "--full-context-csv", dest="full_context_file", type=Path, default=None)
    parser.add_argument("--prompt-template", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--output-prompt", type=Path, default=None)
    parser.add_argument("--output-context-pack", type=Path, default=None)
    parser.add_argument(
        "--extra-config-file",
        action="append",
        type=Path,
        default=None,
        help=(
            "Prompt-only extra config file. May be passed multiple times. Defaults to "
            "project.prompt.extra_config_files, normally configs/jali_emotion_options.yaml "
            "and configs/performance_rules.yaml. configs/llm.yaml is intentionally not used."
        ),
    )
    parser.add_argument(
        "--exact-transcript-file",
        type=Path,
        default=None,
        help=(
            "Optional manually edited exact transcript. If omitted, Step 00 tries "
            "sequence.jali.project_root / sequence.jali.input_dir / {clip_name}.txt."
        ),
    )
    parser.add_argument(
        "--no-auto-exact-transcript-file",
        action="store_true",
        help="Do not auto-load the JALI transcript txt; fall back to full_context subtitle/dialogue text.",
    )
    # Deprecated compatibility: accept but never read candidate_sequence input.
    parser.add_argument("--candidate-jsonl", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _write_text(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing file without --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_exact_transcript(
    args: argparse.Namespace,
    *,
    sequence_config: dict[str, Any],
    repo_root: Path,
) -> tuple[str | None, str]:
    if args.exact_transcript_file is not None:
        path = args.exact_transcript_file
        return path.read_text(encoding="utf-8"), str(path)

    if args.no_auto_exact_transcript_file:
        return None, "full_context subtitle/dialogue (--no-auto-exact-transcript-file)"

    path = transcript_path(sequence_config, repo_root)
    if path.exists():
        return path.read_text(encoding="utf-8"), str(path)

    print(f"WARNING: auto exact transcript file not found: {path}")
    return None, "full_context subtitle/dialogue (auto transcript file missing)"


def _resolve_shot_range(args: argparse.Namespace, sequence_config: dict[str, Any]) -> tuple[int, int]:
    config_start, config_end = shot_range_from_config(sequence_config)
    start = args.start_shot_idx if args.start_shot_idx is not None else config_start
    end = args.end_shot_idx if args.end_shot_idx is not None else config_end
    return int(start), int(end)


def main() -> None:
    args = parse_args()
    sequence_config_path = args.sequence_config_alias or args.sequence_config

    project_config = read_yaml(args.project_config)
    sequence_config = read_yaml(sequence_config_path)
    repo_root = repo_root_from_project_config(args.project_config, project_config)

    seq_section = sequence_section(sequence_config)
    sequence_id = args.sequence_id or sequence_id_from_config(sequence_config)
    if args.sequence_id:
        seq_section["sequence_id"] = args.sequence_id

    movie_id = args.movie_id or movie_id_from_config(sequence_config)
    movie_name = args.movie_name or movie_name_from_config(sequence_config)
    start_shot_idx, end_shot_idx = _resolve_shot_range(args, sequence_config)
    context_window = args.context_window if args.context_window is not None else local_window_from_config(sequence_config)
    full_context_file = args.full_context_file or full_context_path(project_config, sequence_config, repo_root)

    exact_transcript, exact_transcript_source = _load_exact_transcript(
        args,
        sequence_config=sequence_config,
        repo_root=repo_root,
    )
    full_context_rows = load_full_context_records(full_context_file)
    try:
        context_pack = build_context_pack_from_shot_range(
            full_context_rows,
            movie_id=str(movie_id) if movie_id else None,
            movie_name=str(movie_name) if movie_name else None,
            sequence_id=str(sequence_id),
            start_shot_idx=start_shot_idx,
            end_shot_idx=end_shot_idx,
            local_window=context_window,
            exact_transcript=exact_transcript,
        )
    except ValueError as exc:
        raise ValueError(f"{exc}\nSource full_context: {full_context_file}") from exc

    template_path = args.prompt_template or prompt_template_path(project_config, repo_root)
    template = load_prompt_template(template_path)
    extra_config_paths = args.extra_config_file if args.extra_config_file else prompt_extra_config_paths(project_config, repo_root)
    extra_config = load_extra_config_texts(extra_config_paths)
    prompt = build_actor_annotation_prompt(
        prompt_template=template,
        context_pack=context_pack,
        extra_config=extra_config,
    )

    output_dir = args.output_dir or llm_process_dir(project_config, repo_root)
    output_prompt = args.output_prompt or output_dir / f"{sequence_id}__actor_prompt.txt"
    output_context = args.output_context_pack or output_dir / f"{sequence_id}__context_pack.json"

    _write_text(output_prompt, prompt, overwrite=args.overwrite)
    _write_text(output_context, json.dumps(context_pack, ensure_ascii=False, indent=2), overwrite=args.overwrite)

    exact_preview = context_pack.get("exact_transcript", "").replace("\n", " ")[:120]
    print(f"Project config: {args.project_config}")
    print(f"Sequence config: {sequence_config_path}")
    print(f"Context pack: {output_context}")
    print(f"Prompt: {output_prompt}")
    print(f"Source full_context: {full_context_file}")
    print(f"Shot range: {start_shot_idx}-{end_shot_idx}  local_window={context_window}")
    print(f"Annotation mode: actor-style full tag set")
    print(f"Extra config: {[str(path) for path in extra_config_paths]}")
    print(f"Exact transcript source: {exact_transcript_source}")
    print(f"Transcript chars: {len(context_pack.get('exact_transcript', ''))}")
    print(f"Exact transcript preview: {exact_preview}")
    print(f"Full-context rows loaded: {len(full_context_rows)}")
    print("LLM calls: 0")


if __name__ == "__main__":
    main()
