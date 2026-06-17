from __future__ import annotations

import csv
from pathlib import Path

from expregaze_jali.actor_context_builder import (
    build_context_pack_from_shot_range,
    load_full_context_records,
)
from expregaze_jali.actor_prompt_builder import build_actor_annotation_prompt, load_extra_config_texts


def test_build_context_pack_from_full_context_shot_range(tmp_path: Path):
    full_context_path = tmp_path / "full_context.csv"
    with full_context_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "movie_id",
                "movie_name",
                "shot_idx",
                "shot_id",
                "storyline",
                "story_description",
                "subtitle_text",
                "aligned_script_dialogue",
                "aligned_script_text",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "movie_id": "tt0032138",
                "movie_name": "The Wizard of Oz",
                "shot_idx": "37",
                "shot_id": "shot_0037",
                "storyline": "Dorothy runs away and meets Professor Marvel.",
                "story_description": "Professor is trying to send Dorothy home.",
                "subtitle_text": "Previous subtitle.",
                "aligned_script_dialogue": "Previous dialogue.",
                "aligned_script_text": "Professor leads Dorothy toward the wagon.",
            }
        )
        writer.writerow(
            {
                "movie_id": "tt0032138",
                "movie_name": "The Wizard of Oz",
                "shot_idx": "38",
                "shot_id": "shot_0038",
                "storyline": "Dorothy runs away and meets Professor Marvel.",
                "story_description": "Professor is trying to send Dorothy home.",
                "subtitle_text": "That's right. Sit here.",
                "aligned_script_dialogue": "That's right. Here -- sit right down here.",
                "aligned_script_text": "Professor seats Dorothy and shows the crystal.",
            }
        )

    rows = load_full_context_records(full_context_path)
    context_pack = build_context_pack_from_shot_range(
        rows,
        movie_id="tt0032138",
        movie_name="The Wizard of Oz",
        sequence_id="clip_001",
        start_shot_idx=38,
        end_shot_idx=38,
        local_window=1,
        exact_transcript="That's right. Here -- sit right down here.",
    )

    assert context_pack["sequence_id"] == "clip_001"
    assert context_pack["shot_range"]["start_shot_idx"] == 38
    assert "Professor is trying" in context_pack["current_story_description"]
    assert "shows the crystal" in context_pack["current_script_text"]
    assert context_pack["exact_transcript"] == "That's right. Here -- sit right down here."
    assert [row["shot_id"] for row in context_pack["full_context_local_window"]] == ["shot_0037", "shot_0038"]


def test_prompt_builder_injects_extra_config_and_preserves_transcript(tmp_path: Path):
    rules = tmp_path / "performance_rules.yaml"
    emotions = tmp_path / "jali_emotion_options.yaml"
    rules.write_text("performance_rules:\n  note: use sparse tags\n", encoding="utf-8")
    emotions.write_text("jali_emotion:\n  mask:\n    allowed_bearings: [Friendly]\n", encoding="utf-8")

    template = "[SCENE CONTEXT]\n{{context_pack}}\n[EXTRA CONFIG]\n{{extra_config}}\n[EXACT TRANSCRIPT]\n{{transcript}}"
    context_pack = {
        "sequence_id": "clip_001",
        "exact_transcript": "The priests of lsis saw the lnfinite.",
        "scene_targets": {"objects": ["CRYSTAL"]},
    }
    extra_config = load_extra_config_texts([emotions, rules])

    prompt = build_actor_annotation_prompt(
        prompt_template=template,
        context_pack=context_pack,
        extra_config=extra_config,
    )

    assert "The priests of lsis saw the lnfinite." in prompt
    assert "performance_rules.yaml" in prompt
    assert "jali_emotion_options.yaml" in prompt
    assert "base.yaml" not in prompt
    assert "scene_targets" not in prompt
    assert "{{" not in prompt
