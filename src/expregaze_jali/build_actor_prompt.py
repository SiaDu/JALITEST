from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from expregaze_jali.actor_context_builder import (
    build_context_pack_from_shot_range,
    load_full_context_records,
)
from expregaze_jali.actor_prompt_builder import (
    DEFAULT_EXTRA_CONFIG_FILES,
    build_actor_annotation_prompt,
    load_extra_config_texts,
    load_prompt_template,
)


DEFAULT_SEQUENCE_ID = "Jali_proto_candidate_001_ProfessorCrystal"
DEFAULT_TEMPLATE = Path("prompts/actor_performance_annotation_prompt_v2.md")
DEFAULT_PATHS_CONFIG = Path("configs/path_local.yaml")
DEFAULT_OUTPUT_DIR = Path("data/processed/gaze_script/llm_process")
DEFAULT_FULL_CONTEXT = Path("data/processed/full_context/tt0032138__full_context.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Step 00: build context_pack.json and actor prompt from full_context + shot_range. "
            "Does not call the LLM and does not read candidate_sequence files."
        )
    )
    parser.add_argument("--sequence-id", default=None)
    parser.add_argument("--movie-id", default=None)
    parser.add_argument("--movie-name", default=None)
    parser.add_argument("--start-shot-idx", type=int, default=None)
    parser.add_argument("--end-shot-idx", type=int, default=None)
    parser.add_argument("--context-window", "--full-context-window", dest="context_window", type=int, default=None)
    parser.add_argument("--full-context-file", "--full-context-csv", dest="full_context_file", type=Path, default=None)
    parser.add_argument("--prompt-template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--paths-config", type=Path, default=DEFAULT_PATHS_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-prompt", type=Path, default=None)
    parser.add_argument("--output-context-pack", type=Path, default=None)
    parser.add_argument(
        "--extra-config-file",
        action="append",
        type=Path,
        default=None,
        help=(
            "Prompt-only extra config file. May be passed multiple times. Defaults to "
            "configs/jali_emotion_options.yaml and configs/performance_rules.yaml. "
            "configs/base.yaml is intentionally not used for prompt construction."
        ),
    )
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
            "fall back to full_context subtitle/dialogue text."
        ),
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


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return data


def _nested(config: dict[str, Any], *keys: str) -> Any:
    current: Any = config
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _context_config(paths_config: Path) -> dict[str, Any]:
    config = _read_yaml(paths_config)
    context_pack = config.get("context_pack") or {}
    if not isinstance(context_pack, dict):
        raise ValueError("configs/path_local.yaml field `context_pack` must be a mapping")
    jali = config.get("jali") or {}
    if isinstance(jali, dict):
        context_pack = {**context_pack}
        context_pack.setdefault("sequence_id", jali.get("clip_name"))
    return context_pack


def _resolve_config_path(path_value: Any) -> Path | None:
    if path_value is None or str(path_value).strip() == "":
        return None
    return Path(str(path_value))


def _resolve_full_context_path(args: argparse.Namespace, context_cfg: dict[str, Any], movie_id: str | None) -> Path:
    path = args.full_context_file
    if path is None:
        for key in ("full_context_file", "full_context_csv", "full_context_path", "full_context"):
            path = _resolve_config_path(context_cfg.get(key))
            if path is not None:
                break
    if path is None and movie_id:
        path = Path(f"data/processed/full_context/{movie_id}__full_context.csv")
    return path or DEFAULT_FULL_CONTEXT


def _resolve_shot_range(args: argparse.Namespace, context_cfg: dict[str, Any]) -> tuple[int, int]:
    shot_range = context_cfg.get("shot_range") or {}
    if not isinstance(shot_range, dict):
        raise ValueError("configs/path_local.yaml context_pack.shot_range must be a mapping")
    start = args.start_shot_idx
    end = args.end_shot_idx
    if start is None:
        start = _coerce_int(shot_range.get("start_shot_idx"))
    if end is None:
        end = _coerce_int(shot_range.get("end_shot_idx"))
    if start is None or end is None:
        raise ValueError(
            "Missing shot range. Set context_pack.shot_range.start_shot_idx/end_shot_idx "
            "in configs/path_local.yaml, or pass --start-shot-idx and --end-shot-idx."
        )
    return start, end


def _resolve_auto_exact_transcript_file(paths_config: Path, sequence_id: str) -> Path | None:
    """Resolve the default JALI transcript path from configs/path_local.yaml."""
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


def _load_exact_transcript(args: argparse.Namespace, sequence_id: str) -> tuple[str | None, str]:
    if args.exact_transcript_file is not None:
        path = args.exact_transcript_file
        return path.read_text(encoding="utf-8"), str(path)

    if args.no_auto_exact_transcript_file:
        return None, "full_context subtitle/dialogue (--no-auto-exact-transcript-file)"

    path = _resolve_auto_exact_transcript_file(args.paths_config, sequence_id)
    if path is None:
        return None, "full_context subtitle/dialogue (no transcript path in paths config)"

    if path.exists():
        return path.read_text(encoding="utf-8"), str(path)

    print(f"WARNING: auto exact transcript file not found: {path}")
    return None, "full_context subtitle/dialogue (auto transcript file missing)"


def main() -> None:
    args = parse_args()
    context_cfg = _context_config(args.paths_config)

    movie_id = args.movie_id or context_cfg.get("movie_id")
    movie_name = args.movie_name or context_cfg.get("movie_name")
    sequence_id = args.sequence_id or context_cfg.get("sequence_id") or DEFAULT_SEQUENCE_ID
    start_shot_idx, end_shot_idx = _resolve_shot_range(args, context_cfg)
    context_window = args.context_window if args.context_window is not None else int(context_cfg.get("local_window", 3))
    full_context_file = _resolve_full_context_path(args, context_cfg, movie_id)

    exact_transcript, exact_transcript_source = _load_exact_transcript(args, str(sequence_id))
    full_context_rows = load_full_context_records(full_context_file)
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

    template = load_prompt_template(args.prompt_template)
    extra_config_paths = args.extra_config_file if args.extra_config_file else list(DEFAULT_EXTRA_CONFIG_FILES)
    extra_config = load_extra_config_texts(extra_config_paths)
    prompt = build_actor_annotation_prompt(
        prompt_template=template,
        context_pack=context_pack,
        extra_config=extra_config,
    )

    output_prompt = args.output_prompt or args.output_dir / f"{sequence_id}__actor_prompt.txt"
    output_context = args.output_context_pack or args.output_dir / f"{sequence_id}__context_pack.json"

    _write_text(output_prompt, prompt, overwrite=args.overwrite)
    _write_text(output_context, json.dumps(context_pack, ensure_ascii=False, indent=2), overwrite=args.overwrite)

    exact_preview = context_pack.get("exact_transcript", "").replace("\n", " ")[:120]
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
