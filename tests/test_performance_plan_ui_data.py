from __future__ import annotations

import copy
import json
from pathlib import Path
import sys


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from performance_plan_ui_data import (  # noqa: E402
    default_edited_path,
    load_performance_plan,
    save_performance_plan,
    set_event_intent,
    set_event_locks,
    update_affect_span,
    update_gaze_span,
    update_head_span,
    update_lid_state_span,
)


def _plan() -> dict:
    return {
        "schema_version": "performance_plan_v0",
        "sequence_id": "clip",
        "target_character": "ACTOR",
        "unknown_root": {"preserve": True},
        "events": [
            {
                "event_id": "E01",
                "source_intent_tag": "i01",
                "intent": "OPEN",
                "span": {"text": "Hello there.", "char_start": 10, "char_end": 22},
                "evidence": {"transcript": "Hello there."},
                "affect": {
                    "visible": [
                        {
                            "source_tag": "m01",
                            "char_start": 10,
                            "char_end": 15,
                            "value": "Friendly-50",
                            "state": "Friendly",
                            "intensity": 0.5,
                        },
                        {
                            "source_tag": "m02",
                            "char_start": 16,
                            "char_end": 22,
                            "value": "Warm-60",
                            "state": "Warm",
                            "intensity": 0.6,
                        },
                    ],
                    "hidden": [],
                },
                "gaze": [
                    {
                        "source_tag": "g01",
                        "char_start": 10,
                        "char_end": 15,
                        "value": "GAZE-LISTENER",
                        "mode": "GAZE",
                        "target": "LISTENER",
                    },
                    {
                        "source_tag": "g02",
                        "char_start": 16,
                        "char_end": 22,
                        "value": "AVERT-DOWN",
                        "mode": "AVERT",
                        "target": "DOWN",
                    },
                ],
                "head": [
                    {
                        "source_tag": "hd01",
                        "char_start": 10,
                        "char_end": 22,
                        "value": "MEDIUM",
                        "involvement": 0.5,
                    }
                ],
                "lid_state": [
                    {
                        "source_tag": "l01",
                        "char_start": 10,
                        "char_end": 22,
                        "value": "-1",
                        "lid_state": -1,
                    }
                ],
                "blink": {"performative": [], "suppression": []},
                "rationale": {"intent": {"source_tag": "i01", "reason": "opens"}},
                "locks": {"intent": False, "affect": False, "gaze": False, "head": False, "blink": False},
                "unknown_event": ["keep"],
            }
        ],
    }


def test_load_preserves_events_and_default_save_does_not_overwrite_source(tmp_path: Path):
    source = tmp_path / "s029_1talk__performance_plan.json"
    original = _plan()
    source.write_text(json.dumps(original), encoding="utf-8")

    loaded = load_performance_plan(source)
    assert loaded["events"] == original["events"]
    edited = default_edited_path(source)
    assert edited == tmp_path / "s029_1talk__performance_plan_edited.json"
    assert edited != source
    save_performance_plan(loaded, edited)
    assert json.loads(source.read_text(encoding="utf-8")) == original
    assert edited.exists()


def test_edit_helpers_update_only_editable_semantics_and_keep_raw_values_coherent():
    plan = _plan()
    event = plan["events"][0]
    protected = copy.deepcopy(
        {
            "span": event["span"],
            "evidence": event["evidence"],
            "affect_spans": [
                {key: value for key, value in span.items() if key in {"source_tag", "char_start", "char_end"}}
                for span in event["affect"]["visible"]
            ],
            "gaze_spans": [
                {key: value for key, value in span.items() if key in {"source_tag", "char_start", "char_end"}}
                for span in event["gaze"]
            ],
        }
    )

    set_event_intent(event, "CONNECT")
    update_affect_span(event["affect"]["visible"][0], "Angered", 0.75)
    update_gaze_span(event["gaze"][1], "GAZE", "CHARACTER_ALMIRA_GULCH")
    update_head_span(event["head"][0], 0.25)
    update_lid_state_span(event["lid_state"][0], -3)
    set_event_locks(event, {"intent": True, "affect": True, "gaze": False, "head": True, "blink": False})

    assert event["intent"] == "CONNECT"
    assert event["affect"]["visible"][0]["value"] == "Angered-75"
    assert event["gaze"][1]["value"] == "GAZE-CHARACTER_ALMIRA_GULCH"
    assert event["head"][0]["value"] == "LOW"
    assert event["lid_state"][0]["value"] == "-3"
    assert event["locks"]["intent"] is True
    assert event["locks"]["head"] is True
    assert event["span"] == protected["span"]
    assert event["evidence"] == protected["evidence"]
    assert [
        {key: value for key, value in span.items() if key in {"source_tag", "char_start", "char_end"}}
        for span in event["affect"]["visible"]
    ] == protected["affect_spans"]
    assert [
        {key: value for key, value in span.items() if key in {"source_tag", "char_start", "char_end"}}
        for span in event["gaze"]
    ] == protected["gaze_spans"]
    assert len(event["affect"]["visible"]) == 2
    assert len(event["gaze"]) == 2


def test_saving_preserves_unknown_fields(tmp_path: Path):
    plan = _plan()
    destination = tmp_path / "edited.json"

    save_performance_plan(plan, destination)
    saved = json.loads(destination.read_text(encoding="utf-8"))

    assert saved["unknown_root"] == {"preserve": True}
    assert saved["events"][0]["unknown_event"] == ["keep"]


def test_acting_interpretation_is_optional_and_edited_text_survives_save(tmp_path: Path):
    plan = _plan()
    assert "acting_interpretation" not in plan
    plan["acting_interpretation"] = "scene_constraints:\nOne speaker.\n\nnarrative_intent:\nChallenge Gulch."
    destination = tmp_path / "edited.json"
    save_performance_plan(plan, destination)
    loaded = load_performance_plan(destination)
    assert loaded["acting_interpretation"] == plan["acting_interpretation"]


def test_participant_authoring_ui_hides_json_and_file_controls():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    authoring = source.split("    def _build_authoring_tab", 1)[1].split(
        "    def _build_setup", 1
    )[0]
    assert "JSON" not in authoring
    assert "Load Existing Plan" not in authoring
    assert "Save Performance Plan" not in authoring
    assert 'QPushButton("Generate Animation")' in authoring
    assert "Generate or load a performance plan to begin editing." in source
    assert "Load a Performance Plan JSON to author its semantic score." not in source


def test_advanced_debug_retains_canonical_json_load_and_save_controls():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    advanced = source.split("    def _build_advanced_tab", 1)[1].split(
        "    def _select_audio_folder", 1
    )[0]
    assert 'QPushButton("Load Existing Plan...")' in advanced
    assert 'QPushButton("Save Performance Plan")' in advanced
    assert 'QPushButton("Save Performance Plan As...")' in advanced
    assert 'QLabel("Backend Generation Log")' in advanced
    assert '"Performance Plan JSON (*.json)"' in source


def test_setup_exposes_optional_context_and_real_generation_action():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    setup = source.split("    def _build_setup", 1)[1].split(
        "    def _build_acting_interpretation", 1
    )[0]

    assert 'QLabel("Context (Optional)")' in setup
    assert "self.input_context = QtWidgets.QPlainTextEdit()" in setup
    assert "Optional scene, story, character, or performance context." in setup
    assert "self.generate_plan_button.clicked.connect(self.generate_performance_plan)" in setup


def test_participant_setup_has_no_dataset_specific_inputs():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    setup = source.split("    def _build_setup", 1)[1].split(
        "    def _build_acting_interpretation", 1
    )[0]

    for forbidden in (
        "Sequence ID",
        "Movie ID",
        "Movie Name",
        "Shot Range",
        "Full Context",
        "Context Window",
        "Local Window",
        "sequence_config",
        "movie_id",
        "shot_start",
        "shot_end",
    ):
        assert forbidden not in setup


def test_generation_ui_reports_progress_and_loads_backend_result():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    generation = source.split("    def generate_performance_plan", 1)[1].split(
        "    def _known_look_targets", 1
    )[0]

    assert 'setText("Generating performance plan...")' in generation
    assert "self.backend_runner.start(" in generation
    assert "self.load_plan(path)" in generation
    assert 'setText("Performance plan generated.")' in generation
    assert 'setText("Performance plan generation failed.")' in generation
