from __future__ import annotations

import argparse
import json
from pathlib import Path

from expregaze_jali.actor_context_builder import (
    build_actor_context_pack,
    find_candidate,
    load_full_context_window,
)
from expregaze_jali.actor_prompt_builder import (
    build_actor_annotation_prompt,
    get_capability_profile,
    load_extra_config_texts,
    load_prompt_template,
)


DEFAULT_SEQUENCE_ID = "Jali_proto_candidate_001_ProfessorCrystal"
DEFAULT_CANDIDATES = Path("data/processed/candidate_sequences/Jali_proto_candidate_sequences.jsonl")
DEFAULT_FULL_CONTEXT = Path("data/processed/full_context/tt0032138__full_context.csv")
DEFAULT_TEMPLATE = Path("prompts/actor_performance_annotation_prompt_v2.md")
DEFAULT_BASE_CONFIG = Path("configs/base.yaml")
DEFAULT_JALI_OPTIONS = Path("configs/jali_emotion_options.yaml")
DEFAULT_OUTPUT_DIR = Path("data/processed/gaze_script/prompt")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build compact LLM prompt for actor-style ExpreGaze-JALI performance annotation."
    )
    parser.add_argument("--sequence-id", default=DEFAULT_SEQUENCE_ID)
    parser.add_argument("--candidate-jsonl", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--full-context-csv", type=Path, default=DEFAULT_FULL_CONTEXT)
    parser.add_argument("--full-context-window", type=int, default=2)
    parser.add_argument("--no-full-context", action="store_true")
    parser.add_argument("--prompt-template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--base-config", type=Path, default=DEFAULT_BASE_CONFIG)
    parser.add_argument("--jali-emotion-options", type=Path, default=DEFAULT_JALI_OPTIONS)
    parser.add_argument("--profile", choices=["mvp", "full_actor"], default="full_actor")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-prompt", type=Path, default=None)
    parser.add_argument("--output-context-pack", type=Path, default=None)
    return parser.parse_args()


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

    context_pack = build_actor_context_pack(candidate, full_rows)
    profile = get_capability_profile(args.profile)
    template = load_prompt_template(args.prompt_template)
    extra_config = load_extra_config_texts(
        base_config=args.base_config,
        jali_emotion_options=args.jali_emotion_options,
    )

    prompt = build_actor_annotation_prompt(
        prompt_template=template,
        context_pack=context_pack,
        capability_profile=profile,
        extra_config=extra_config,
    )

    output_prompt = args.output_prompt or args.output_dir / f"{args.sequence_id}__actor_prompt.txt"
    output_context = args.output_context_pack or args.output_dir / f"{args.sequence_id}__context_pack.json"

    output_prompt.parent.mkdir(parents=True, exist_ok=True)
    output_context.parent.mkdir(parents=True, exist_ok=True)

    output_prompt.write_text(prompt, encoding="utf-8")
    output_context.write_text(json.dumps(context_pack, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Prompt: {output_prompt}")
    print(f"Context pack: {output_context}")
    print(f"Profile: {args.profile}")
    print(f"Transcript chars: {len(context_pack.get('exact_transcript', ''))}")
    print(f"Full-context rows: {len(full_rows)}")
    print(f"Scene targets: {context_pack.get('scene_targets')}")


if __name__ == "__main__":
    main()
