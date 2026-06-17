from __future__ import annotations

from expregaze_jali.actor_overlay_event_exporter import export_actor_overlay_events
from expregaze_jali.run_actor_annotator import _build_openai_request


def test_export_actor_overlay_events_filters_non_overlay_types():
    resolved = {
        "events": [
            {
                "id": "g01",
                "type": "gaze",
                "value": "GAZE-LISTENER",
                "text": "Hello",
                "resolved_time": {"start": 0.0, "end": 1.0},
            },
            {
                "id": "l01",
                "type": "lid_state",
                "value": "-1",
                "text": "Hello",
                "reason": "controlled alertness",
                "span": {"start": 0, "end": 5},
                "resolved_time": {"start": 0.0, "end": 1.0},
            },
            {
                "id": "pb01",
                "type": "performative_blink",
                "value": "EYE_CLOSE_HOLD-HYPNOTIC_CUE",
                "text": "close your eyes",
                "reason": "demonstrates the instruction",
                "span": {"start": 10, "end": 25},
                "resolved_time": {"start": 2.0, "end": 3.0},
            },
        ],
        "diagnostics": {"unresolved_events": []},
    }

    exported = export_actor_overlay_events(resolved, clip_name="clip")

    assert exported["clip_name"] == "clip"
    assert [event["id"] for event in exported["events"]] == ["l01", "pb01"]
    assert exported["events"][0]["type"] == "lid_state"
    assert exported["events"][1]["value"] == "EYE_CLOSE_HOLD-HYPNOTIC_CUE"


def test_gpt5_request_omits_temperature():
    request = _build_openai_request(
        "prompt",
        {"model": "gpt-5-mini", "temperature": 0.2, "max_output_tokens": 3000},
    )

    assert request["model"] == "gpt-5-mini"
    assert request["max_output_tokens"] == 3000
    assert "temperature" not in request


def test_non_gpt5_request_keeps_temperature():
    request = _build_openai_request(
        "prompt",
        {"model": "gpt-4.1-mini", "temperature": 0.2, "max_output_tokens": 3000},
    )

    assert request["temperature"] == 0.2
