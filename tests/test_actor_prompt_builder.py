from __future__ import annotations

import csv
from pathlib import Path

from expregaze_jali.actor_context_builder import (
    build_context_pack_from_shot_range,
    load_full_context_records,
)
from expregaze_jali.actor_prompt_builder import build_actor_annotation_prompt, load_extra_config_texts


def test_build_context_pack_from_new_full_context_schema(tmp_path: Path):
    full_context_path = tmp_path / "full_context.csv"
    fieldnames = [
        "movie_id",
        "meta_movie_name",
        "meta_story_overview",
        "meta_storyline",
        "anno_story_id",
        "anno_story_description",
        "shot_id",
        "annotation_subtitle",
        "aligned_script_dialogue",
        "aligned_speakers",
        "aligned_script_text",
        "shot_type",
        "match_score",
    ]
    base = {
        "movie_id": "tt0032138",
        "meta_movie_name": "The Wizard of Oz",
        "meta_story_overview": "Famous musical film.",
        "meta_storyline": "Dorothy and Toto are carried to Oz after a tornado.",
        "anno_story_id": "tt0032138_0000",
        "anno_story_description": "Dorothy meets Professor Marvel, who tries to send her home.",
        "aligned_speakers": '["PROFESSOR"]',
        "shot_type": "MLS",
        "match_score": "90",
    }
    with full_context_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                **base,
                "shot_id": "shot_0037",
                "annotation_subtitle": '["Previous subtitle."]',
                "aligned_script_dialogue": "Previous dialogue.",
                "aligned_script_text": "Professor leads Dorothy toward the wagon.",
            }
        )
        writer.writerow(
            {
                **base,
                "shot_id": "shot_0038",
                "annotation_subtitle": '["That\'s right. Here.", "Sit right down here."]',
                "aligned_script_dialogue": "That's right. Here -- sit right down here.",
                "aligned_script_text": "Professor seats Dorothy and shows the crystal.",
            }
        )
        writer.writerow(
            {
                **base,
                "shot_id": "shot_0039",
                "annotation_subtitle": '["Next subtitle."]',
                "aligned_script_dialogue": "Next dialogue.",
                "aligned_script_text": "Next shot should stay in local window only.",
            }
        )

    rows = load_full_context_records(full_context_path)
    context_pack = build_context_pack_from_shot_range(
        rows,
        movie_id="tt0032138",
        sequence_id="clip_001",
        start_shot_idx=38,
        end_shot_idx=38,
        local_window=1,
        exact_transcript="<mask=Friendly-80>That's right.</mask=Friendly-80>",
    )

    assert context_pack["movie_name"] == "The Wizard of Oz"
    assert "tornado" in context_pack["storyline"]
    assert "Professor Marvel" in context_pack["current_story_description"]
    assert context_pack["current_script_text"] == "Professor seats Dorothy and shows the crystal."
    assert "Next shot" not in context_pack["current_script_text"]
    assert context_pack["subtitle_text"] == "That's right. Here. Sit right down here."
    assert context_pack["aligned_script_dialogue"] == "That's right. Here -- sit right down here."
    assert context_pack["exact_transcript"] == "That's right."
    assert [row["shot_id"] for row in context_pack["full_context_local_window"]] == [
        "shot_0037",
        "shot_0038",
        "shot_0039",
    ]


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
