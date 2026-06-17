from __future__ import annotations

import csv
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
)


def test_build_context_pack_uses_candidate_transcript_and_story_card(tmp_path: Path):
    candidate_path = tmp_path / "candidates.jsonl"
    full_context_path = tmp_path / "full_context.csv"

    candidate = {
        "sequence_id": "seq_001",
        "movie_id": "tt0032138",
        "prototype_label": "Professor crystal-ball monologue",
        "start_shot_idx": 38,
        "end_shot_idx": 38,
        "start_time_hms": "00:12:46.724",
        "end_time_hms": "00:13:22.010",
        "total_sec": 35.285,
        "shot_count": 1,
        "active_speakers": ["PROFESSOR"],
        "why_selected_for_jali_proto": "Single speaker with crystal target.",
        "script_action_preview": "Professor guides Dorothy to the crystal.",
        "shots": [
            {
                "shot_idx": 38,
                "shot_id": "shot_0038",
                "subtitle_text": "That's right. The priests of lsis saw the lnfinite.",
                "aligned_script_dialogue": "That's right. The priests of Isis saw the infinite.",
                "prev_other_text": "Professor rises near the wagon.",
                "next_other_text": "Photograph insert.",
            }
        ],
    }
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")

    with full_context_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "movie_id",
                "story_description",
                "shot_idx",
                "shot_id",
                "shot_start_time_hms",
                "shot_end_time_hms",
                "subtitle_text",
                "aligned_script_dialogue",
                "aligned_speakers",
                "prev_other_text",
                "next_other_text",
                "match_score",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "movie_id": "tt0032138",
                "story_description": "Professor Marvel is a phony fortune teller trying to send Dorothy home.",
                "shot_idx": "38",
                "shot_id": "shot_0038",
                "shot_start_time_hms": "00:12:46.724",
                "shot_end_time_hms": "00:13:22.010",
                "subtitle_text": "That's right.",
                "aligned_script_dialogue": "That's right.",
                "aligned_speakers": '["PROFESSOR"]',
                "prev_other_text": "Professor rises.",
                "next_other_text": "Photograph insert.",
                "match_score": "0.9",
            }
        )

    loaded = find_candidate(candidate_path, "seq_001")
    rows = load_full_context_window(full_context_path, "tt0032138", 38, 38, window=1)
    context_pack = build_actor_context_pack(loaded, rows)

    assert "phony fortune teller" in context_pack["story_card"]
    assert "lsis" in context_pack["exact_transcript"]
    assert "lnfinite" in context_pack["exact_transcript"]
    assert "CRYSTAL" in context_pack["scene_targets"]["objects"]


def test_prompt_builder_injects_profile_and_preserves_transcript():
    template = "[CONTEXT PACK]\n{{context_pack}}\n[CAPABILITY PROFILE]\n{{capability_profile}}\n[EXTRA CONFIG]\n{{extra_config}}\n[EXACT TRANSCRIPT]\n{{transcript}}"
    context_pack = {
        "sequence_id": "seq_001",
        "exact_transcript": "The priests of lsis saw the lnfinite.",
    }
    profile = get_capability_profile("mvp")

    prompt = build_actor_annotation_prompt(
        prompt_template=template,
        context_pack=context_pack,
        capability_profile=profile,
        extra_config={"note": "test"},
    )

    assert "The priests of lsis saw the lnfinite." in prompt
    assert "performative_blink" in prompt
    assert "disabled_tags" in prompt
    assert "mvp" in prompt
