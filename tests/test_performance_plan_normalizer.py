from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from expregaze_jali.performance_annotation_parser import parse_performance_annotation
from expregaze_jali.performance_plan_normalizer import normalize_performance_plan


def _write_annotation(
    tmp_path: Path, annotation: str, reasons: str, *, analyze: str = "ok"
) -> Path:
    path = tmp_path / "performance_annotation.txt"
    path.write_text(
        f"[ANALYZE]\n\n{analyze}\n\n[ANNOTATION]\n\n{annotation}\n\n[REASONS]\n\n{reasons}\n",
        encoding="utf-8",
    )
    return path


def test_acting_interpretation_preserves_analyze_section_verbatim(tmp_path: Path):
    analyze = (
        "scene_constraints:\nOne speaker and one prop.\n\n"
        "affective_cognitive_state:\nControlled anger.\n\n"
        "narrative_intent:\nChallenge authority."
    )
    parsed = parse_performance_annotation(
        _write_annotation(
            tmp_path,
            "<i01=CONFRONT>Line.</i01>",
            "i01=CONFRONT: challenges authority",
            analyze=analyze,
        )
    )
    plan = normalize_performance_plan(parsed, sequence_id="s_test", target_character="ACTOR")
    assert parsed["analyze"] == analyze
    assert plan["acting_interpretation"] == analyze


def _parse_and_normalize(
    tmp_path: Path,
    annotation: str,
    reasons: str,
    *,
    exact_transcript: str | None = None,
) -> tuple[dict, dict]:
    parsed = parse_performance_annotation(_write_annotation(tmp_path, annotation, reasons))
    context = {"target_character": "ACTOR"}
    if exact_transcript is not None:
        context["exact_transcript"] = exact_transcript
    return parsed, normalize_performance_plan(
        parsed,
        sequence_id="s_test",
        context_pack=context,
    )


def test_intent_uses_stable_span_lists_for_single_and_multiple_states(tmp_path: Path):
    annotation = (
        "<i01=CONFRONT_AUTHORITY><m01=Provoked-70>First.</m01> "
        "<m02=Angered-85>Second.</m02> "
        "<g01=GAZE-CHARACTER_GULCH>Third.</g01> "
        "<g02=AVERT-DOWN>Fourth.</g02></i01>"
    )
    reasons = "\n".join(
        [
            "i01=CONFRONT_AUTHORITY: sustains the confrontation",
            "m01=Provoked-70: begins with indignation",
            "m02=Angered-85: intensifies the grievance",
            "g01=GAZE-CHARACTER_GULCH: holds direct pressure",
            "g02=AVERT-DOWN: turns inward to restrain the thought",
        ]
    )
    parsed, plan = _parse_and_normalize(
        tmp_path,
        annotation,
        reasons,
        exact_transcript="First. Second. Third. Fourth.",
    )

    event = plan["events"][0]
    assert event["affect"]["visible"] == [
        {
            "source_tag": "m01",
            "char_start": 0,
            "char_end": 6,
            "value": "Provoked-70",
            "state": "Provoked",
            "intensity": 0.7,
        },
        {
            "source_tag": "m02",
            "char_start": 7,
            "char_end": 14,
            "value": "Angered-85",
            "state": "Angered",
            "intensity": 0.85,
        },
    ]
    assert event["gaze"] == [
        {
            "source_tag": "g01",
            "char_start": 15,
            "char_end": 21,
            "value": "GAZE-CHARACTER_GULCH",
            "mode": "GAZE",
            "target": "CHARACTER_GULCH",
        },
        {
            "source_tag": "g02",
            "char_start": 22,
            "char_end": 29,
            "value": "AVERT-DOWN",
            "mode": "AVERT",
            "target": "DOWN",
        },
    ]
    assert isinstance(event["affect"]["hidden"], list)
    assert isinstance(event["head"], list)
    assert isinstance(event["lid_state"], list)
    assert isinstance(event["blink"]["performative"], list)
    assert parsed["clean_transcript"] == "First. Second. Third. Fourth."


def test_state_spans_are_exact_and_rationale_is_field_addressable(tmp_path: Path):
    annotation = (
        "<i01=CONNECT><hd01=MEDIUM><l01=-1><m01=Friendly-66>"
        "Hello.</m01></l01></hd01><h01=Warm-30> Goodbye.</h01></i01>"
    )
    reasons = "\n".join(
        [
            "i01=CONNECT: reaches for connection",
            "hd01=MEDIUM: gives the greeting physical weight",
            "l01=-1: keeps the attention alert",
            "m01=Friendly-66: makes the greeting open",
            "h01=Warm-30: retains private warmth at the goodbye",
        ]
    )
    _parsed, plan = _parse_and_normalize(
        tmp_path,
        annotation,
        reasons,
        exact_transcript="Hello. Goodbye.",
    )

    event = plan["events"][0]
    assert event["head"] == [
        {
            "source_tag": "hd01",
            "char_start": 0,
            "char_end": 6,
            "value": "MEDIUM",
            "involvement": 0.5,
        }
    ]
    assert event["lid_state"][0]["char_start"] == 0
    assert event["lid_state"][0]["char_end"] == 6
    assert event["affect"]["hidden"][0]["char_start"] == 6
    assert event["affect"]["hidden"][0]["char_end"] == 15
    assert event["rationale"] == {
        "intent": {"source_tag": "i01", "reason": "reaches for connection"},
        "affect": {
            "visible": [{"source_tag": "m01", "reason": "makes the greeting open"}],
            "hidden": [{"source_tag": "h01", "reason": "retains private warmth at the goodbye"}],
        },
        "gaze": [],
        "head": [{"source_tag": "hd01", "reason": "gives the greeting physical weight"}],
        "lid_state": [{"source_tag": "l01", "reason": "keeps the attention alert"}],
        "blink": {"performative": [], "suppression": []},
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [("NONE", 0.0), ("LOW", 0.25), ("MEDIUM", 0.5), ("HIGH", 0.75), ("FULL", 1.0)],
)
def test_head_involvement_mapping(tmp_path: Path, value: str, expected: float):
    _parsed, plan = _parse_and_normalize(
        tmp_path,
        f"<i01=TEST><hd01={value}>Line.</hd01></i01>",
        f"i01=TEST: test beat\nhd01={value}: meaningful head involvement",
        exact_transcript="Line.",
    )
    assert plan["events"][0]["head"][0]["involvement"] == expected


def test_exact_transcript_equality_and_mismatch_diagnostic(tmp_path: Path):
    annotation = "<i01=TEST>Exact transcript.</i01>"
    reasons = "i01=TEST: test beat"
    _parsed, matching = _parse_and_normalize(
        tmp_path,
        annotation,
        reasons,
        exact_transcript="Exact transcript.",
    )
    assert not matching["diagnostics"]["errors"]

    _parsed, mismatching = _parse_and_normalize(
        tmp_path,
        annotation,
        reasons,
        exact_transcript="Exact tranxcript.",
    )
    assert mismatching["diagnostics"]["errors"] == [
        "exact transcript mismatch at character 10: annotation='s', context='x'"
    ]


def test_intent_events_keep_deterministic_numbering(tmp_path: Path):
    _parsed, plan = _parse_and_normalize(
        tmp_path,
        "<i01=OPEN>One.</i01> <i02=CLOSE>Two.</i02>",
        "i01=OPEN: opens the exchange\ni02=CLOSE: closes the exchange",
        exact_transcript="One. Two.",
    )
    assert [event["event_id"] for event in plan["events"]] == ["E01", "E02"]


def test_locks_default_false_and_plan_has_no_timing_domain_fields(tmp_path: Path):
    _parsed, plan = _parse_and_normalize(
        tmp_path,
        "<i01=TEST><bs01=SUPPRESS><pb01=SLOW_BLINK>Line.</pb01></bs01></i01>",
        "\n".join(
            [
                "i01=TEST: test beat",
                "pb01=SLOW_BLINK: an intentional pause",
                "bs01=SUPPRESS: sustains the deliberate restraint",
            ]
        ),
        exact_transcript="Line.",
    )
    assert all(value is False for event in plan["events"] for value in event["locks"].values())
    assert plan["events"][0]["blink"] == {
        "performative": [
            {"source_tag": "pb01", "char_start": 0, "char_end": 5, "value": "SLOW_BLINK"}
        ],
        "suppression": [
            {"source_tag": "bs01", "char_start": 0, "char_end": 5, "value": "SUPPRESS"}
        ],
    }

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert not {"seconds", "frames", "time", "timing"}.intersection(keys(plan))
    json.dumps(plan)


def test_malformed_duplicate_closing_tag_recovery_is_independent_of_luna(tmp_path: Path):
    parsed, plan = _parse_and_normalize(
        tmp_path,
        "<i01=WITHHOLD><h01=Contempt-68>And now.</h01></h01></i01>",
        "i01=WITHHOLD: restrains the insult\nh01=Contempt-68: keeps it private",
        exact_transcript="And now.",
    )
    assert parsed["diagnostics"]["duplicate_closing_tags"][0]["id"] == "h01"
    assert any("duplicate closing tag: </h01>" in warning for warning in plan["diagnostics"]["warnings"])
    assert plan["events"][0]["affect"]["hidden"][0]["state"] == "Contempt"


def test_redundant_consecutive_same_state_is_diagnosed(tmp_path: Path):
    path = _write_annotation(
        tmp_path,
        "<i01=EXPLAIN><g01=GAZE-CHARACTER_A>One.</g01> <g02=GAZE-CHARACTER_A>Two.</g02></i01>",
        "i01=EXPLAIN: one beat\ng01: first gaze\ng02: redundant gaze",
    )
    parsed = parse_performance_annotation(path)
    assert parsed["diagnostics"]["redundant_same_state_tags"][0]["id"] == "g02"


def test_invalid_head_and_unmatched_structure_are_not_silently_discarded(tmp_path: Path):
    parsed, plan = _parse_and_normalize(
        tmp_path,
        "</g09><i01=TEST><hd01=EXTREME>Line.</i01>",
        "i01=TEST: test beat\nhd01=EXTREME: invalid source value",
        exact_transcript="Line.",
    )
    assert parsed["diagnostics"]["unmatched_closing_tags"][0]["id"] == "g09"
    assert parsed["diagnostics"]["unclosed_opening_tags"][0]["id"] == "hd01"
    assert parsed["diagnostics"]["invalid_head_values"][0]["value"] == "EXTREME"
    assert any("invalid hd value" in error for error in plan["diagnostics"]["errors"])
    assert plan["events"][0]["head"][0]["involvement"] is None


def test_build_performance_plan_cli_reports_paths_and_counts(tmp_path: Path):
    annotation = _write_annotation(
        tmp_path,
        "<i01=CONNECT><hd01=LOW>Hello.</hd01></i01>",
        "i01=CONNECT: opens contact\nhd01=LOW: lightly supports the greeting",
    )
    context = tmp_path / "context.json"
    context.write_text(
        json.dumps({"target_character": "ACTOR", "exact_transcript": "Hello."}),
        encoding="utf-8",
    )
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
    assert "Events: 1" in result.stdout
    assert "Errors: 0" in result.stdout
    assert "Warnings: 0" in result.stdout
    assert json.loads(output.read_text(encoding="utf-8"))["events"][0]["event_id"] == "E01"
