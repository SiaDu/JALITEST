from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from expregaze_jali.performance_annotation_parser import parse_performance_annotation
from expregaze_jali.performance_plan_normalizer import normalize_performance_plan


def _write_annotation(tmp_path: Path, annotation: str, reasons: str) -> Path:
    path = tmp_path / "performance_annotation.txt"
    path.write_text(
        f"[ANALYZE]\n\nok\n\n[ANNOTATION]\n\n{annotation}\n\n[REASONS]\n\n{reasons}\n",
        encoding="utf-8",
    )
    return path


def _plan(tmp_path: Path) -> tuple[dict, dict]:
    path = _write_annotation(
        tmp_path,
        (
            "<i01=CONFRONT_AUTHORITY><g01=GAZE-CHARACTER_GULCH>"
            "<m01=Provoked-78><hd01=MEDIUM>Exact first beat.</hd01></m01></g01></i01> "
            "<i02=WITHHOLD_INSULT><h01=Contempt-68><l01=3>"
            "<pb01=SLOW_BLINK><bs01=SUPPRESS>Exact second beat.</bs01></pb01>"
            "</l01></h01></i02>"
        ),
        "\n".join(
            [
                "i01=CONFRONT_AUTHORITY: challenges Gulch directly",
                "g01=GAZE-CHARACTER_GULCH: holds social pressure",
                "m01=Provoked-78: makes the challenge visible",
                "hd01=MEDIUM: gives the challenge physical weight",
                "i02=WITHHOLD_INSULT: stops before crossing the social line",
                "h01=Contempt-68: preserves the sharper inner judgment",
                "l01=3: closes off while self-censoring",
                "pb01=SLOW_BLINK: marks the decision not to speak",
                "bs01=SUPPRESS: sustains restraint after the choice",
            ]
        ),
    )
    parsed = parse_performance_annotation(path)
    return parsed, normalize_performance_plan(parsed, sequence_id="s_test", target_character="AUNT_EM")


def test_intent_beats_nested_states_and_exact_transcript_spans(tmp_path: Path):
    parsed, plan = _plan(tmp_path)

    assert [event["event_id"] for event in plan["events"]] == ["E01", "E02"]
    first, second = plan["events"]
    assert first["source_intent_tag"] == "i01"
    assert first["intent"] == "CONFRONT_AUTHORITY"
    assert first["affect"]["visible"] == {"state": "Provoked", "intensity": 0.78}
    assert first["gaze"] == {"mode": "GAZE", "target": "CHARACTER_GULCH"}
    assert second["affect"]["hidden"] == {"state": "Contempt", "intensity": 0.68}
    assert second["lid_state"] == 3
    assert second["blink"] == {"performative": "SLOW_BLINK", "suppression": "SUPPRESS"}

    clean = parsed["clean_transcript"]
    for event in plan["events"]:
        span = event["span"]
        exact = clean[span["char_start"] : span["char_end"]]
        assert span["text"] == exact
        assert event["evidence"]["transcript"] == exact


@pytest.mark.parametrize(
    ("value", "expected"),
    [("NONE", 0.0), ("LOW", 0.25), ("MEDIUM", 0.5), ("HIGH", 0.75), ("FULL", 1.0)],
)
def test_head_involvement_mapping(tmp_path: Path, value: str, expected: float):
    path = _write_annotation(
        tmp_path,
        f"<i01=TEST><hd01={value}>Line.</hd01></i01>",
        f"i01=TEST: test beat\nhd01={value}: meaningful head involvement",
    )
    parsed = parse_performance_annotation(path)
    plan = normalize_performance_plan(parsed, sequence_id="s_test", target_character="ACTOR")
    assert plan["events"][0]["head"]["involvement"] == expected


def test_locks_default_false_and_plan_has_no_timing_domain_fields(tmp_path: Path):
    _parsed, plan = _plan(tmp_path)
    assert all(value is False for event in plan["events"] for value in event["locks"].values())

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert not {"seconds", "frames"}.intersection(keys(plan))
    json.dumps(plan)


def test_current_luna_annotation_recovers_duplicate_closing_tag():
    fixture = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "processed"
        / "gaze_script"
        / "llm_process"
        / "s029_1talk__performance_annotation.txt"
    )
    parsed = parse_performance_annotation(fixture)

    duplicates = parsed["diagnostics"]["duplicate_closing_tags"]
    assert any(item["id"] == "h01" for item in duplicates)
    assert any("duplicate closing tag: </h01>" in warning for warning in parsed["diagnostics"]["warnings"])
    assert parsed["reasons"]["h01"].startswith("The withheld final judgment")
    assert "And now" in parsed["clean_transcript"]


def test_redundant_consecutive_same_state_is_diagnosed(tmp_path: Path):
    path = _write_annotation(
        tmp_path,
        (
            "<i01=EXPLAIN><g01=GAZE-CHARACTER_A>One.</g01> "
            "<g02=GAZE-CHARACTER_A>Two.</g02></i01>"
        ),
        "i01=EXPLAIN: one beat\ng01: first gaze\ng02: redundant gaze",
    )
    parsed = parse_performance_annotation(path)

    redundant = parsed["diagnostics"]["redundant_same_state_tags"]
    assert redundant == [
        {
            "previous_id": "g01",
            "id": "g02",
            "type": "gaze",
            "value": "GAZE-CHARACTER_A",
            "position": 5,
        }
    ]


def test_invalid_head_and_unmatched_structure_are_not_silently_discarded(tmp_path: Path):
    path = _write_annotation(
        tmp_path,
        "</g09><i01=TEST><hd01=EXTREME>Line.</i01>",
        "i01=TEST: test beat\nhd01=EXTREME: invalid source value",
    )
    parsed = parse_performance_annotation(path)
    plan = normalize_performance_plan(parsed, sequence_id="s_test", target_character="ACTOR")

    assert parsed["diagnostics"]["unmatched_closing_tags"][0]["id"] == "g09"
    assert parsed["diagnostics"]["unclosed_opening_tags"][0]["id"] == "hd01"
    assert parsed["diagnostics"]["invalid_head_values"][0]["value"] == "EXTREME"
    assert any("invalid hd value" in error for error in plan["diagnostics"]["errors"])
    assert plan["events"][0]["head"]["involvement"] is None


def test_build_performance_plan_cli_reports_paths_and_counts(tmp_path: Path):
    annotation = _write_annotation(
        tmp_path,
        "<i01=CONNECT><hd01=LOW>Hello.</hd01></i01>",
        "i01=CONNECT: opens contact\nhd01=LOW: lightly supports the greeting",
    )
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"target_character": "ACTOR"}), encoding="utf-8")
    output = tmp_path / "plan.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "expregaze_jali.build_performance_plan",
            "--sequence-id",
            "s_cli",
            "--annotation",
            str(annotation),
            "--context",
            str(context),
            "--output",
            str(output),
            "--overwrite",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"Annotation: {annotation}" in result.stdout
    assert f"Context: {context}" in result.stdout
    assert "Events: 1" in result.stdout
    assert "Errors: 0" in result.stdout
    assert "Warnings: 0" in result.stdout
    assert f"Performance Plan: {output}" in result.stdout
    assert json.loads(output.read_text(encoding="utf-8"))["events"][0]["event_id"] == "E01"
