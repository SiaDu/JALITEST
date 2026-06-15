from __future__ import annotations

import json
from pathlib import Path

from expregaze_jali.gaze_event_exporter import export_gaze_events
from expregaze_jali.jali_annotation_exporter import export_jali_annotation
from expregaze_jali.performance_annotation_parser import parse_performance_annotation
from expregaze_jali.performance_event_compiler import compile_state_change_events
from expregaze_jali.performance_event_resolver import load_words_jsonl, resolve_events_with_textgrid

ROOT = Path(__file__).resolve().parents[1]
CLIP = "Jali_proto_candidate_001_ProfessorCrystal"
ANNOTATION = ROOT / "data/processed/gaze_script/llm_process" / f"{CLIP}__script.txt"
WORDS = ROOT / "data/processed/textgrid" / f"{CLIP}__words.jsonl"


def _pipeline():
    parsed = parse_performance_annotation(ANNOTATION)
    compiled = compile_state_change_events(parsed)
    resolved = resolve_events_with_textgrid(compiled, load_words_jsonl(WORDS))
    return parsed, compiled, resolved


def test_parse_sections_tags_and_preserve_transcript_words():
    parsed = parse_performance_annotation(ANNOTATION)

    assert {"ANALYZE", "ANNOTATION", "REASONS"} <= set(parsed["sections"])
    assert parsed["tags"][0]["id"] == "m01"
    assert parsed["tags"][1]["id"] == "g01"
    assert not parsed["diagnostics"]["missing_reasons"]
    assert "lsis" in parsed["clean_transcript"]
    assert "lnfinite" in parsed["clean_transcript"]
    assert "<g01=" not in parsed["clean_transcript"]


def test_compile_state_change_spans_to_next_same_type_tag():
    parsed, compiled, _resolved = _pipeline()
    gaze = {event["id"]: event for event in compiled["gaze"]}
    mask = {event["id"]: event for event in compiled["mask"]}

    assert gaze["g01"]["text"] == "That's right."
    assert gaze["g02"]["text"] == "Here."
    assert mask["m01"]["text"].startswith("That's right.")
    assert mask["m01"]["text"].endswith("That's it.")
    assert compiled["clean_transcript"] == parsed["clean_transcript"]


def test_resolve_first_and_last_gaze_times():
    _parsed, _compiled, resolved = _pipeline()
    gaze = {event["id"]: event for event in resolved["gaze"]}

    assert gaze["g01"]["resolved_time"]["start"] == 0.19
    assert gaze["g01"]["resolved_time"]["end"] == 1.0
    assert gaze["g13"]["resolved_time"]["start"] == 33.651558
    assert gaze["g13"]["resolved_time"]["end"] == 35.241562
    assert not resolved["diagnostics"]["unresolved_events"]


def test_jali_exporter_removes_gaze_and_pairs_mask_tags():
    parsed, _compiled, resolved = _pipeline()
    jali_text = export_jali_annotation(parsed, resolved)

    assert "<g01=" not in jali_text
    assert "<mask=Friendly-66>" in jali_text
    assert "</mask=Friendly-66>" in jali_text
    assert "That's right." in jali_text


def test_gaze_exporter_splits_mode_target_and_is_json_serializable():
    _parsed, _compiled, resolved = _pipeline()
    exported = export_gaze_events(resolved, clip_name=CLIP)

    assert exported["clip_name"] == CLIP
    assert exported["events"][0]["mode"] == "GAZE"
    assert exported["events"][0]["target"] == "LISTENER"
    assert exported["events"][0]["resolved_time"]["source"] == "textgrid_words"
    json.dumps(exported)
