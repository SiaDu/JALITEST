from __future__ import annotations

import json
from pathlib import Path

import pytest

from expregaze_jali.compile_performance_plan import (
    compile_performance_plan,
    discover_timing_alignment,
)


SCRIPT = "Hello there."


def _plan(mask: str = "Friendly-66") -> dict:
    end = len(SCRIPT)
    return {
        "schema_version": "performance_plan_v0",
        "sequence_id": "run_test",
        "target_character": "ACTOR",
        "source_annotation": "performance_annotation.txt",
        "events": [
            {
                "event_id": "E01",
                "source_intent_tag": "i01",
                "span": {"text": SCRIPT, "char_start": 0, "char_end": end},
                "intent": "WELCOME",
                "affect": {
                    "visible": [
                        {
                            "source_tag": "human_phrase_1_affect",
                            "char_start": 0,
                            "char_end": 5,
                            "value": mask,
                            "state": mask.rsplit("-", 1)[0],
                            "intensity": 0.66,
                            "author": "human",
                        }
                    ],
                    "hidden": [],
                },
                "gaze": [
                    {
                        "source_tag": "human_phrase_1_gaze",
                        "char_start": 0,
                        "char_end": 5,
                        "value": "GAZE-DOWN",
                        "mode": "GAZE",
                        "target": "DOWN",
                        "author": "human",
                    }
                ],
                "head": [
                    {
                        "source_tag": "human_phrase_1_head",
                        "char_start": 0,
                        "char_end": 5,
                        "value": "LOW",
                        "involvement": 0.25,
                        "author": "human",
                    }
                ],
                "lid_state": [
                    {
                        "source_tag": "human_phrase_1_lid",
                        "char_start": 0,
                        "char_end": 5,
                        "value": "-1",
                        "lid_state": -1,
                        "author": "human",
                    }
                ],
                "blink": {"performative": [], "suppression": []},
                "rationale": {
                    "intent": {"source_tag": "i01", "reason": "Greets."},
                    "affect": {"visible": [], "hidden": []},
                    "gaze": [],
                    "head": [],
                    "lid_state": [],
                    "blink": {"performative": [], "suppression": []},
                },
                "evidence": {"transcript": SCRIPT},
                "locks": {
                    "intent": False,
                    "affect": False,
                    "gaze": False,
                    "head": False,
                    "blink": False,
                },
            }
        ],
        "diagnostics": {"errors": [], "warnings": []},
    }


def _write_words(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "run__words.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"word": "Hello", "norm": "hello", "start": 0.0, "end": 0.5},
                {"word": "there", "norm": "there", "start": 0.6, "end": 1.0},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_plan(path: Path, plan: dict | None = None) -> Path:
    path.write_text(json.dumps(plan or _plan(), indent=2), encoding="utf-8")
    return path


def test_timing_discovery_prefers_words_jsonl(tmp_path: Path):
    words_path = _write_words(tmp_path)
    (tmp_path / "fallback.TextGrid").write_text("not selected", encoding="utf-8")

    timing = discover_timing_alignment(tmp_path)

    assert timing.kind == "words_jsonl"
    assert timing.path == words_path.resolve()
    assert len(timing.words) == 2


def test_timing_discovery_supports_textgrid(tmp_path: Path):
    path = tmp_path / "dialogue.TextGrid"
    path.write_text(
        '''File type = "ooTextFile"\nObject class = "TextGrid"\n\n'''
        '''item [1]:\n    class = "IntervalTier"\n    name = "words"\n'''
        '''    intervals [1]:\n        xmin = 0\n        xmax = 0.5\n        text = "Hello"\n'''
        '''    intervals [2]:\n        xmin = 0.6\n        xmax = 1.0\n        text = "there"\n''',
        encoding="utf-8",
    )

    timing = discover_timing_alignment(tmp_path)

    assert timing.kind == "textgrid"
    assert [word["norm"] for word in timing.words] == ["hello", "there"]


def test_timing_discovery_fails_clearly_without_alignment(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="No timing alignment file found"):
        discover_timing_alignment(tmp_path)


def test_compiler_uses_edited_plan_and_never_original_annotation(tmp_path: Path):
    plan_path = _write_plan(tmp_path / "performance_plan.json", _plan("Angry-80"))
    (tmp_path / "performance_annotation.txt").write_text(
        "<m01=Friendly-10>Hello</m01> there.", encoding="utf-8"
    )
    audio = tmp_path / "audio"
    _write_words(audio)
    output = tmp_path / "animation"

    manifest = compile_performance_plan(
        performance_plan_path=plan_path,
        script=SCRIPT,
        audio_folder=audio,
        output_dir=output,
        overwrite=True,
    )

    jali = (output / "annotated_for_jali.txt").read_text(encoding="utf-8")
    assert "<mask=Angry-80>" in jali
    assert "Friendly-10" not in jali
    assert manifest["source"] == "canonical_performance_plan"
    debug = json.loads((output / "compile_from_plan_debug.txt").read_text(encoding="utf-8"))
    assert debug["original_performance_annotation_used"] is False
    gaze = json.loads((output / "gaze_events_resolved.json").read_text(encoding="utf-8"))
    assert gaze["events"][-1]["target"] == "__BASE__"
    assert gaze["events"][-1]["resolved_time"]["source"] == "canonical_span_end"
    eye = json.loads((output / "eye_performance_events.json").read_text(encoding="utf-8"))
    assert eye["lid_state_events"][-1]["value"] == 0
    assert eye["lid_state_events"][-1]["resolved_time"]["source"] == "canonical_span_end"
    head = json.loads((output / "head_events_resolved.json").read_text(encoding="utf-8"))
    assert head["events"][0]["value"] == "LOW"
    assert head["diagnostics"]["maya_apply_status"] == "not_implemented"


def test_compiler_rejects_script_that_disagrees_with_canonical_spans(tmp_path: Path):
    plan_path = _write_plan(tmp_path / "performance_plan.json")
    audio = tmp_path / "audio"
    _write_words(audio)

    with pytest.raises(ValueError, match="does not match canonical event"):
        compile_performance_plan(
            performance_plan_path=plan_path,
            script="Goodbye now.",
            audio_folder=audio,
            output_dir=tmp_path / "animation",
            overwrite=True,
        )


def test_compiler_rejects_timing_alignment_that_does_not_match_script(tmp_path: Path):
    plan_path = _write_plan(tmp_path / "performance_plan.json")
    audio = tmp_path / "audio"
    words = _write_words(audio)
    words.write_text(
        json.dumps({"word": "Goodbye", "norm": "goodbye", "start": 0.0, "end": 1.0})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match the timing alignment"):
        compile_performance_plan(
            performance_plan_path=plan_path,
            script=SCRIPT,
            audio_folder=audio,
            output_dir=tmp_path / "animation",
            overwrite=True,
        )
