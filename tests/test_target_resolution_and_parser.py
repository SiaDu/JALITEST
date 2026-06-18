from pathlib import Path

from expregaze_jali.gaze_event_exporter import export_gaze_events
from expregaze_jali.performance_annotation_parser import parse_performance_annotation


def test_parser_strips_closing_tags(tmp_path: Path):
    p = tmp_path / "annotation.txt"
    p.write_text(
        """[ANALYZE]\n\nok\n\n[ANNOTATION]\n\n<g1=GAZE-LISTENER>Hello</g1> <m1=Friendly-70>there</m1>\n\n[REASONS]\n\ng1: listener gaze\nm1: friendly mask\n""",
        encoding="utf-8",
    )
    parsed = parse_performance_annotation(p)
    assert parsed["clean_transcript"].strip() == "Hello there"
    assert len(parsed["tags"]) == 2
    assert len(parsed["diagnostics"]["stripped_closing_tags"]) == 2


def test_parser_recovers_bare_tags_and_renumbers_duplicate_ids(tmp_path: Path):
    p = tmp_path / "annotation.txt"
    p.write_text(
        """[ANALYZE]\n\nok\n\n[ANNOTATION]\n\nl01=-1 Hello <m01=Friendly-70>there</m01> <m01=Friendly-70>again</m01> <g01=GAZE-CHARACTER_DOROTHY>now</g01> <g01=GAZE-CHARACTER_DOROTHY>later</g01>\n\n[REASONS]\n\nl01=-1: lid baseline\nm01=Friendly-70: friendly mask\ng01=GAZE-CHARACTER_DOROTHY: Dorothy gaze\n""",
        encoding="utf-8",
    )
    parsed = parse_performance_annotation(p)
    ids = [tag["id"] for tag in parsed["tags"]]
    assert ids == ["l01", "m01", "m02", "g01", "g02"]
    assert "l01=-1" not in parsed["clean_transcript"]
    assert parsed["diagnostics"]["normalized_bare_tags"]
    assert len(parsed["diagnostics"]["renumbered_duplicate_tag_ids"]) == 2
    assert not parsed["diagnostics"]["missing_reasons"]


def test_gaze_exporter_resolves_listener_and_marks_object():
    resolved = {
        "events": [
            {"id": "g1", "type": "gaze", "value": "GAZE-LISTENER", "text": "hello", "resolved_time": {"start": 0, "end": 1}},
            {"id": "g2", "type": "gaze", "value": "GLANCE-OBJECT", "text": "prop", "resolved_time": {"start": 1, "end": 2}},
        ],
        "diagnostics": {},
    }
    context = {
        "scene_targets": {"people": ["DOROTHY", "PROFESSOR"], "objects": ["CRYSTAL", "PHOTOGRAPH"], "directions": ["DOWN"]},
        "target_context": {"role_map": {"LISTENER": "DOROTHY"}},
    }
    exported = export_gaze_events(resolved, clip_name="clip", context_pack=context)
    assert exported["events"][0]["target_label"] == "DOROTHY"
    assert exported["events"][0]["target_needs_resolution"] is False
    assert exported["events"][1]["target"] == "OBJECT"
    assert exported["events"][1]["target_needs_resolution"] is True
    assert exported["diagnostics"]["generic_targets"]
