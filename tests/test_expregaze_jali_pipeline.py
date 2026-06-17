from __future__ import annotations

import json
from pathlib import Path

from expregaze_jali.gaze_event_exporter import export_gaze_events
from expregaze_jali.jali_annotation_exporter import export_jali_annotation
from expregaze_jali.performance_annotation_parser import parse_performance_annotation
from expregaze_jali.performance_event_compiler import compile_state_change_events
from expregaze_jali.performance_event_resolver import resolve_events_with_textgrid


def _annotation_file(tmp_path: Path) -> Path:
    path = tmp_path / "annotation.txt"
    path.write_text(
        """[ANALYZE]\n\nok\n\n[ANNOTATION]\n\n<g01=GAZE-LISTENER><m01=Friendly-66>Hello there</m01></g01> <g02=GLANCE-CRYSTAL>prop now</g02>\n\n[REASONS]\n\ng01=GAZE-LISTENER: listener gaze establishes contact\nm01=Friendly-66: friendly mask softens the line\ng02=GLANCE-CRYSTAL: glance sells the prop\n""",
        encoding="utf-8",
    )
    return path


def _words() -> list[dict]:
    return [
        {"word": "Hello", "norm": "hello", "start": 0.0, "end": 0.5},
        {"word": "there", "norm": "there", "start": 0.5, "end": 1.0},
        {"word": "prop", "norm": "prop", "start": 1.0, "end": 1.5},
        {"word": "now", "norm": "now", "start": 1.5, "end": 2.0},
    ]


def _pipeline(tmp_path: Path):
    parsed = parse_performance_annotation(_annotation_file(tmp_path))
    compiled = compile_state_change_events(parsed)
    resolved = resolve_events_with_textgrid(compiled, _words())
    return parsed, compiled, resolved


def test_parse_sections_tags_and_preserve_transcript_words(tmp_path: Path):
    parsed = parse_performance_annotation(_annotation_file(tmp_path))

    assert {"ANALYZE", "ANNOTATION", "REASONS"} <= set(parsed["sections"])
    assert [tag["id"] for tag in parsed["tags"]] == ["g01", "m01", "g02"]
    assert not parsed["diagnostics"]["missing_reasons"]
    assert parsed["clean_transcript"].strip() == "Hello there prop now"
    assert "<g01=" not in parsed["clean_transcript"]


def test_compile_uses_explicit_closing_tags(tmp_path: Path):
    parsed, compiled, _resolved = _pipeline(tmp_path)
    gaze = {event["id"]: event for event in compiled["gaze"]}
    mask = {event["id"]: event for event in compiled["mask"]}

    assert gaze["g01"]["text"] == "Hello there"
    assert gaze["g02"]["text"] == "prop now"
    assert mask["m01"]["text"] == "Hello there"
    assert compiled["clean_transcript"] == parsed["clean_transcript"]


def test_resolve_gaze_times(tmp_path: Path):
    _parsed, _compiled, resolved = _pipeline(tmp_path)
    gaze = {event["id"]: event for event in resolved["gaze"]}

    assert gaze["g01"]["resolved_time"]["start"] == 0.0
    assert gaze["g01"]["resolved_time"]["end"] == 1.0
    assert gaze["g02"]["resolved_time"]["start"] == 1.0
    assert gaze["g02"]["resolved_time"]["end"] == 2.0
    assert not resolved["diagnostics"]["unresolved_events"]


def test_jali_exporter_removes_gaze_and_pairs_mask_tags(tmp_path: Path):
    parsed, _compiled, resolved = _pipeline(tmp_path)
    jali_text = export_jali_annotation(parsed, resolved)

    assert "<g01=" not in jali_text
    assert "<mask=Friendly-66>" in jali_text
    assert "</mask=Friendly-66>" in jali_text
    assert "Hello there" in jali_text


def test_gaze_exporter_splits_mode_target_and_is_json_serializable(tmp_path: Path):
    _parsed, _compiled, resolved = _pipeline(tmp_path)
    exported = export_gaze_events(resolved, clip_name="clip")

    assert exported["clip_name"] == "clip"
    assert exported["events"][0]["mode"] == "GAZE"
    assert exported["events"][0]["target"] == "LISTENER"
    assert exported["events"][0]["resolved_time"]["source"] == "textgrid_words"
    json.dumps(exported)
