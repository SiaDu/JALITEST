from __future__ import annotations

from expregaze_jali.actor_context_builder import build_context_pack_from_shot_range


def _row(shot_id: str, story: str, script: str, subtitle: str = "") -> dict[str, str]:
    return {
        "movie_id": "tt0032138",
        "meta_movie_name": "The Wizard of Oz",
        "meta_storyline": "Global storyline.",
        "anno_story_description": story,
        "shot_id": shot_id,
        "aligned_script_text": script,
        "annotation_subtitle": subtitle,
        "aligned_script_dialogue": script,
    }


def test_full_story_description_collects_unique_story_descriptions_in_order():
    rows = [
        _row("shot_0037", "Story A", "script 37"),
        _row("shot_0038", "Story A", "script 38", '["line one", "line two"]'),
        _row("shot_0040", "Story B", "script 40"),
        _row("shot_0041", "Story B", "script 41"),
        _row("shot_0045", "Story C", "script 45"),
    ]

    pack = build_context_pack_from_shot_range(
        rows,
        movie_id="tt0032138",
        sequence_id="clip_38",
        start_shot_idx=38,
        end_shot_idx=38,
        local_window=1,
    )

    assert pack["movie_name"] == "The Wizard of Oz"
    assert pack["storyline"] == "Global storyline."
    assert pack["current_story_description"] == "Story A"
    assert pack["full_story_description"] == "Story A | Story B | Story C"
    assert pack["current_script_text"] == "script 38"
    assert "script 37" not in pack["current_script_text"]
    assert "script 40" not in pack["current_script_text"]
    assert pack["subtitle_text"] == "line one line two"
