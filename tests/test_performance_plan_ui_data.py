from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import sys

import pytest


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from performance_plan_ui_data import (  # noqa: E402
    canonical_v2_authored_content,
    default_edited_path,
    is_v2_plan_changed_from_loaded_snapshot,
    is_v2_plan_edited,
    load_performance_plan,
    save_animation_runtime_plan,
    save_performance_plan,
    score_text_matches_clean_baseline,
    set_event_intent,
    set_event_locks,
    update_affect_span,
    update_gaze_span,
    update_head_span,
    update_lid_state_span,
)
from performance_score_model import PerformanceScoreModel  # noqa: E402


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
                            "value": "Friendly-60",
                            "state": "Friendly",
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
    assert edited == tmp_path / "s029_1talk__performance_plan_edited_01.json"
    assert edited != source
    save_performance_plan(loaded, edited)
    assert json.loads(source.read_text(encoding="utf-8")) == original
    assert edited.exists()
    assert default_edited_path(source) == tmp_path / "s029_1talk__performance_plan_edited_02.json"


def test_load_accepts_dual_v0_and_v1_phrase_plans_but_rejects_malformed_v1(tmp_path: Path):
    phrase = {"phrase_id": "P01", "states": {"ALICE": {}, "BOB": {}}}
    v0 = tmp_path / "dual_v0.json"
    v0.write_text(json.dumps({"schema_version": "dual_performance_plan_v0", "characters": {"A": "ALICE", "B": "BOB"}, "phrases": [phrase]}), encoding="utf-8")
    assert load_performance_plan(v0)["phrases"] == [phrase]

    v1 = tmp_path / "dual_v1.json"
    v1.write_text(json.dumps({"schema_version": "dual_performance_plan_v1", "characters": ["ALICE", "BOB"], "phrases": [phrase]}), encoding="utf-8")
    assert load_performance_plan(v1)["characters"] == ["ALICE", "BOB"]

    missing_phrases = tmp_path / "missing_phrases.json"
    missing_phrases.write_text(json.dumps({"schema_version": "dual_performance_plan_v1", "characters": ["ALICE", "BOB"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="phrases list"):
        load_performance_plan(missing_phrases)

    invalid_characters = tmp_path / "invalid_characters.json"
    invalid_characters.write_text(json.dumps({"schema_version": "dual_performance_plan_v1", "characters": ["ALICE"], "phrases": [phrase]}), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly two named characters"):
        load_performance_plan(invalid_characters)


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
    assert "self.generate_animation_button.clicked.connect(self.generate_animation)" in authoring
    assert "self.generate_animation_button.setEnabled(False)" not in authoring
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
    assert "Event Metadata" not in advanced
    assert "self.diagnostics" not in advanced
    assert "QSplitter" not in advanced
    assert '"Performance Plan JSON (*.json)"' in source


def test_score_clean_baseline_tracks_only_real_text_changes():
    baseline = {"ALICE": "Hello <Happy-60>", "BOB": "No <Neutral-60>"}
    assert score_text_matches_clean_baseline(dict(baseline), baseline)
    assert not score_text_matches_clean_baseline(
        {**baseline, "ALICE": "Hello <Angered-60>"}, baseline
    )
    assert score_text_matches_clean_baseline(dict(baseline), baseline)
    assert not score_text_matches_clean_baseline(baseline, None)


def test_ui_suppresses_programmatic_score_updates_and_resets_clean_baseline():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    load = source.split("    def load_plan(", 1)[1].split(
        "    def _refresh_phrase_reason", 1
    )[0]
    apply = source.split("    def apply_score_edits", 1)[1].split(
        "    def _score_payload", 1
    )[0]

    assert "self._suppress_score_dirty_tracking = True" in source
    assert "finally:\n            self._suppress_score_dirty_tracking = False" in source
    assert "score_text_matches_clean_baseline(" in source
    assert "self._set_score_editor_text(self.score_editor" in load
    assert "self._mark_score_editors_clean()" in load
    assert "self._mark_score_editors_clean()" in apply


def test_setup_exposes_optional_context_and_real_generation_action():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    setup = source.split("    def _build_setup", 1)[1].split("    def _build_semantic_score", 1)[0]

    assert 'QLabel("Acting Direction (Optional)")' in setup
    assert "self.input_context = QtWidgets.QPlainTextEdit()" in setup
    assert "Optional acting direction, scene information, character motivation, or performance constraints." in setup
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
        "    def _invalidate_generated_presentation", 1
    )[0]
    generation_start = generation
    generation = source.split("    def generate_performance_plan", 1)[1].split(
        "    def _known_look_targets", 1
    )[0]

    assert "current plan preserved until replacement succeeds" in generation
    assert "self.backend_runner.start(" in generation
    assert "self.load_plan(path, preserve_authoring_text=True)" in source
    assert 'setText("Performance plan generated — animation setup incomplete.")' in generation
    assert "Performance Plan generated with" in source
    assert "Performance plan generation failed â€” previous plan preserved." in generation
    assert "self._invalidate_generated_presentation()" not in generation_start
    assert "self.plan = None" not in generation_start
    assert "self.score_model = None" not in generation_start
    assert "self.source_path = None" not in generation_start


def test_generation_failure_paths_preserve_the_active_plan_until_successful_load():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    generation = source.split("    def generate_performance_plan", 1)[1].split(
        "    def _invalidate_generated_presentation", 1
    )[0]
    succeeded = source.split("    def _generation_succeeded", 1)[1].split(
        "    def _generation_failed", 1
    )[0]
    failed = source.split("    def _generation_failed", 1)[1].split(
        "    def _look_at_mapping_data", 1
    )[0]
    assert "except Exception as exc:\n            self._generation_failed(str(exc))" in generation
    assert "self.load_plan(path, preserve_authoring_text=True)" in succeeded
    assert "self._generation_had_active_plan = False" in succeeded
    assert "self._invalidate_generated_presentation" not in failed
    assert "self.plan = None" not in failed
    assert "self.score_model = None" not in failed
    assert "self.source_path = None" not in failed


def test_generate_animation_uses_current_score_runtime_plan_and_real_handler():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    animation = source.split("    def generate_animation", 1)[1].split(
        "    def _animation_compile_succeeded", 1
    )[0]

    assert "self.validate_score(show_dialog=True)" in animation
    assert "save_animation_runtime_plan(" in animation
    assert "self._score_payload()" in animation
    assert "self.animation_runner.start(" in animation
    assert 'setText("Generating animation...")' in animation
    assert "self._generate_dual_speaker_emotion()" in animation
    assert "start_dual(" in animation
    assert "performance_annotation" not in animation
    assert "sequence_config" not in animation


def test_authoring_session_restores_script_and_context_in_ui_source():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    restore = source.split("    def _restore_authoring_session", 1)[1].split(
        "    def _build_authoring_session_data", 1
    )[0]
    build = source.split("    def _build_authoring_session_data", 1)[1].split(
        "    def _save_authoring_session_for_path", 1
    )[0]

    assert 'session.get("input_script")' in restore
    assert 'session.get("input_context")' in restore
    assert "input_script=self.input_script.toPlainText()" in build
    assert "input_context=self.input_context.toPlainText()" in build


def test_dual_v2_uses_actor_calibration_and_discards_legacy_jali_baselines():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    refresh = source.split("    def _refresh_required_look_at_targets", 1)[1].split(
        "    def _capture_dual_look_at", 1
    )[0]
    restore = source.split("    def _restore_authoring_session", 1)[1].split(
        "    def _build_authoring_session_data", 1
    )[0]
    assert '"dual_performance_plan_v2"' in refresh
    assert 'saved_baseline.get("schema_version") == "dual_jali_base_v2"' in restore
    assert "Discarded legacy JALI baseline" in restore


def test_generation_success_loads_plan_without_reformatting_authoring_text():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    succeeded = source.split("    def _generation_succeeded", 1)[1].split(
        "    def _generation_failed", 1
    )[0]
    load = source.split("    def load_plan(", 1)[1].split(
        "    def _refresh_phrase_reason", 1
    )[0]

    assert "self.load_plan(path, preserve_authoring_text=True)" in succeeded
    assert "input_script.setPlainText" not in succeeded
    assert "input_context.setPlainText" not in succeeded
    assert "not preserve_authoring_text" in load


def test_maya_launcher_clears_stale_local_helper_modules_before_loading_ui():
    source = (MAYA_TOOLS / "run_performance_plan_ui.py").read_text(encoding="utf-8")
    assert '"performance_plan_ui_data"' in source
    assert '"performance_score_model"' in source
    assert "sys.modules.pop(name, None)" in source
    assert "Path(cached_path).resolve().parent == tools_root" in source


def test_multiline_editors_share_score_font_and_use_larger_sizes():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")

    assert "def _configure_multiline_editor(" in source
    assert "QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)" in source
    for editor, height in (
        ("input_script", 240),
        ("input_context", 200),
            ("score_editor", 260),
            ("phrase_reason", 240),
            ("backend_log", 180),
            ("validation_details", 120),
    ):
        assert re.search(
            rf"_configure_multiline_editor\(\s*self\.{editor},\s*height={height}",
            source,
        )


def test_semantic_score_editors_resize_from_wrapped_document_layout():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    assert "def _resize_semantic_score_editor(" in source
    assert "document.setTextWidth(max(1, editor.viewport().width()))" in source
    assert "document.documentLayout().documentSize().height()" in source
    assert "_SCORE_EDITOR_MIN_HEIGHT = 260" in source
    assert "_SCORE_EDITOR_MAX_HEIGHT = 650" in source
    assert "ScrollBarAlwaysOff" in source and "ScrollBarAsNeeded" in source
    assert "self._schedule_semantic_score_editor_resize()" in source
    assert "editor.viewport().installEventFilter(self)" in source
    assert "self.score_editor_b.show()" in source
    assert "self._resize_semantic_score_editors()" in source


def test_animation_runtime_plan_applies_valid_dirty_score_before_saving(tmp_path: Path):
    model = PerformanceScoreModel(_plan())
    edited_score = model.score_text.replace("<Friendly-50>", "<Angered-80>", 1)
    runtime_path = tmp_path / "animation" / "performance_plan_runtime.json"

    updated = save_animation_runtime_plan(model, edited_score, runtime_path)
    saved = json.loads(runtime_path.read_text(encoding="utf-8"))

    assert updated["events"][0]["affect"]["visible"][0]["value"] == "Angered-80"
    assert saved == updated
    assert saved["authoring"]["manually_edited_phrases"][0]["changed_categories"] == [
        "affect"
    ]


def test_dual_ui_removes_acting_interpretation_and_regenerate_placeholder():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    assert "ACTING INTERPRETATION" not in source
    assert "Regenerate Plan" not in source
    assert "Confirm Original Reason" not in source and "Replace Animator Reason" not in source


def test_dual_mode_setup_does_not_access_validation_widgets_before_creation():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    assert 'if dual and hasattr(self, "validation_label"):' in source


def test_loading_dual_v2_forces_hidden_mode_back_to_dual_after_session_restore():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    load = source.split('if loaded_plan.get("schema_version") == "dual_performance_plan_v2":', 1)[1].split(
        'elif loaded_plan.get("schema_version")', 1
    )[0]
    assert "self.mode_combo.setCurrentIndex(1)" in load
    assert "self._update_character_mode()" in load


def test_v2_ui_tracks_loaded_snapshot_baseline_only_after_load_or_save_paths():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    dual_animation = source.split("    def _generate_dual_speaker_emotion", 1)[1].split(
        "    def _animation_compile_succeeded", 1
    )[0]
    save = source.split("    def _save_to", 1)[1].split("\n\ndef show_performance", 1)[0]
    assert "self._loaded_v2_snapshot_content" in source
    assert "is_v2_plan_changed_from_loaded_snapshot(candidate_plan, loaded_content)" in dual_animation
    assert "self.source_path = runtime_plan" in dual_animation
    assert "self._loaded_v2_snapshot_content = canonical_v2_authored_content(candidate_plan)" in dual_animation
    assert "self.source_path = path" in save
    assert "self._loaded_v2_snapshot_content = canonical_v2_authored_content(self.plan or {})" in save


def test_sparse_highlighter_discounts_initial_display_separator_from_projection_offsets():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    highlighter = source.split("class _SparseScoreHighlighter", 1)[1].split("class PerformancePlanEditor", 1)[0]
    assert "projection_offset_from_score_plain_offset(" in highlighter


def test_debug_tab_remains_available_for_runtime_diagnostics():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    assert 'self.tabs.addTab(advanced, "Advanced / Debug")' in source
    assert "self.tabs.setTabVisible" not in source


def test_restore_jali_base_surfaces_neutral_mismatch_as_a_non_blocking_warning():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    restore = source.split("    def _restore_jali_base", 1)[1].split(
        "    def _known_look_targets", 1
    )[0]
    assert "Restore warning:" in restore
    assert "restored with gaze-neutral warning" in restore


def test_dual_ui_uses_performance_tag_wording_and_panel_relative_dialogue_roles():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    assert "SEMANTIC PERFORMANCE TAG" in source
    assert 'QPushButton("Validate Tag")' in source
    assert 'QPushButton("Apply Tag Edits")' in source
    assert "def panel_dialogue_role(panel_actor: str, speaker: str)" in source
    assert "speaker_key(panel_actor) == speaker_key(speaker)" in source
    assert "panel_actor=first" in source and "panel_actor=second" in source
    assert "first character = yellow" not in source


def test_dual_ui_separates_editable_initial_and_dialogue_performance():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    assert 'QLabel("INITIAL PERFORMANCE")' in source
    assert 'QLabel("DIALOGUE PERFORMANCE")' in source
    assert "self.initial_score_editor" in source and "self.initial_score_editor_b" in source
    assert '"initial": self.initial_score_editor.toPlainText()' in source
    assert '"dialogue": self.score_editor.toPlainText()' in source


def test_v2_runtime_plan_saves_edited_semantics_without_reason_confirmation(tmp_path: Path):
    from tools.maya.dual_sparse_score_model import DualSparseScoreModel
    from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model

    anchors = build_conversation_anchor_model("ALICE: Hello.\nBOB: No.", character_a="ALICE", character_b="BOB")
    plan = {"schema_version": "dual_performance_plan_v2", "characters": ["ALICE", "BOB"],
            "initial_states": {"ALICE": {"affect": "Watchful-60", "gaze": "GAZE-BOB"}, "BOB": {"affect": "Neutral-60", "gaze": "GAZE-ALICE"}},
            "initial_reasons": {"ALICE": "Guarded.", "BOB": "Settled."},
            "tracks": {"ALICE": [], "BOB": [{"event_id": "E1", "anchor_id": "w0002", "changes": {"affect": "Nervous-60"}, "reason": "The refusal unsettles her."}]}}
    model = DualSparseScoreModel(plan, anchors)
    texts = dict(model.score_texts)
    texts["BOB"] = texts["BOB"].replace("<Nervous-60>", "<Happy-60>")
    saved = save_animation_runtime_plan(model, texts, tmp_path / "performance_plan_edited_01.json")
    assert saved["tracks"]["BOB"][0]["reason_status"] == "stale_after_user_edit"
    assert saved["tracks"]["BOB"][0]["reason"] == "The refusal unsettles her."


def test_v2_canonical_snapshots_are_immutable_and_unedited_runtime_reuses_original(tmp_path: Path):
    plan = {"schema_version": "dual_performance_plan_v2", "characters": ["ALICE", "BOB"], "tracks": {"ALICE": [], "BOB": []}}
    original = tmp_path / "performance_plan.json"
    save_performance_plan(plan, original)
    with pytest.raises(ValueError, match="Immutable v2"):
        save_performance_plan(plan, original)
    edited = tmp_path / "performance_plan_edited_01.json"
    save_performance_plan(plan, edited)
    with pytest.raises(ValueError, match="Immutable v2"):
        save_performance_plan(plan, edited)
    assert default_edited_path(original).name == "performance_plan_edited_02.json"


def test_v2_edit_detection_only_snapshots_actual_animator_edits():
    original = {"schema_version": "dual_performance_plan_v2", "characters": ["ALICE", "BOB"], "initial_states": {"ALICE": {"affect": "Watchful-60"}, "BOB": {"affect": "Neutral-60"}}, "initial_reasons": {"ALICE": "One.", "BOB": "Two."}, "tracks": {"ALICE": [{"event_id": "E1", "actor": "ALICE", "anchor_id": "w0001", "changes": {"gaze": "GAZE-BOB"}, "reason": "Looks."}], "BOB": []}}
    current = copy.deepcopy(original)
    current["provenance"] = {"original_authored_content": canonical_v2_authored_content(original)}
    assert is_v2_plan_edited(current, original) is False
    current["tracks"]["ALICE"] = []
    assert is_v2_plan_edited(current, original) is True
    assert is_v2_plan_edited({**current, "acting_interpretation": "Different display text."}, original) is True


def test_v2_canonical_comparison_detects_every_authored_change_but_ignores_display_metadata():
    original = {"schema_version": "dual_performance_plan_v2", "characters": ["ALICE", "BOB"], "initial_states": {"ALICE": {"affect": "Watchful-60"}, "BOB": {"affect": "Neutral-60"}}, "initial_reasons": {"ALICE": "One.", "BOB": "Two."}, "tracks": {"ALICE": [{"event_id": "E1", "actor": "ALICE", "anchor_id": "w0001", "changes": {"gaze": "GAZE-BOB", "head": "HEAD-NONE"}, "reason": "Looks."}], "BOB": []}}
    baseline = canonical_v2_authored_content(original)
    unchanged = {**copy.deepcopy(original), "acting_interpretation": "Read-only context.", "provenance": {"original_authored_content": baseline}}
    assert not is_v2_plan_edited(unchanged, original)
    variants = []
    added = copy.deepcopy(unchanged); added["tracks"]["BOB"].append({"actor": "BOB", "anchor_id": "w0001", "changes": {"head": "HEAD-NONE"}, "reason": "Adds."}); variants.append(added)
    removed_channel = copy.deepcopy(unchanged); removed_channel["tracks"]["ALICE"][0]["changes"].pop("head"); variants.append(removed_channel)
    changed = copy.deepcopy(unchanged); changed["tracks"]["ALICE"][0]["changes"]["gaze"] = "GAZE-DOWN"; variants.append(changed)
    moved = copy.deepcopy(unchanged); moved["tracks"]["ALICE"][0]["anchor_id"] = "w0002"; variants.append(moved)
    changed_reason = copy.deepcopy(unchanged); changed_reason["tracks"]["ALICE"][0]["reason"] = "Changed."; variants.append(changed_reason)
    changed_initial = copy.deepcopy(unchanged); changed_initial["initial_states"]["ALICE"]["affect"] = "Happy-60"; variants.append(changed_initial)
    changed_initial_reason = copy.deepcopy(unchanged); changed_initial_reason["initial_reasons"]["ALICE"] = "Changed."; variants.append(changed_initial_reason)
    assert all(is_v2_plan_edited(plan, original) for plan in variants)


def test_v2_gaze_target_candidates_are_metadata_not_authored_content():
    original = {"schema_version": "dual_performance_plan_v2", "characters": ["ALICE", "BOB"], "gaze_target_candidates": ["LETTER"], "initial_states": {"ALICE": {}, "BOB": {}}, "initial_reasons": {"ALICE": None, "BOB": None}, "tracks": {"ALICE": [], "BOB": []}}
    changed = {**copy.deepcopy(original), "gaze_target_candidates": ["WINDOW", "DOOR"]}
    assert canonical_v2_authored_content(changed) == canonical_v2_authored_content(original)


def test_v2_loaded_snapshot_comparison_is_distinct_from_immutable_llm_baseline():
    original = {"schema_version": "dual_performance_plan_v2", "characters": ["ALICE", "BOB"], "initial_states": {"ALICE": {"gaze": "GAZE-BOB"}, "BOB": {"gaze": "GAZE-ALICE"}}, "initial_reasons": {"ALICE": "One.", "BOB": "Two."}, "tracks": {"ALICE": [], "BOB": []}}
    original["provenance"] = {"original_authored_content": canonical_v2_authored_content(original)}
    edited_01 = copy.deepcopy(original)
    edited_01["initial_states"]["ALICE"]["gaze"] = "GAZE-DOWN"
    unchanged = copy.deepcopy(edited_01)
    assert not is_v2_plan_changed_from_loaded_snapshot(unchanged, edited_01)

    changed_again = copy.deepcopy(edited_01)
    changed_again["initial_states"]["ALICE"]["gaze"] = "GAZE-RIGHT"
    assert is_v2_plan_changed_from_loaded_snapshot(changed_again, edited_01)

    reverted_to_llm = copy.deepcopy(edited_01)
    reverted_to_llm["initial_states"]["ALICE"]["gaze"] = "GAZE-BOB"
    assert not is_v2_plan_edited(reverted_to_llm, original)
    assert is_v2_plan_changed_from_loaded_snapshot(reverted_to_llm, edited_01)


def test_v2_loaded_snapshot_comparison_ignores_metadata_and_advances_with_snapshot_sequence(tmp_path: Path):
    loaded = {"schema_version": "dual_performance_plan_v2", "characters": ["ALICE", "BOB"], "initial_states": {"ALICE": {"gaze": "GAZE-BOB"}, "BOB": {"gaze": "GAZE-ALICE"}}, "initial_reasons": {"ALICE": "One.", "BOB": "Two."}, "tracks": {"ALICE": [], "BOB": []}}
    metadata_only = copy.deepcopy(loaded)
    metadata_only.update({"gaze_target_candidates": ["WINDOW"], "diagnostics": {"warnings": ["note"]}, "provenance": {"anything": "ignored"}})
    assert not is_v2_plan_changed_from_loaded_snapshot(metadata_only, loaded)

    original_path = tmp_path / "performance_plan.json"
    assert default_edited_path(original_path).name == "performance_plan_edited_01.json"
    edited_01 = copy.deepcopy(loaded); edited_01["initial_states"]["ALICE"]["gaze"] = "GAZE-DOWN"
    assert is_v2_plan_changed_from_loaded_snapshot(edited_01, loaded)
    active = copy.deepcopy(edited_01)
    assert not is_v2_plan_changed_from_loaded_snapshot(edited_01, active)
    (tmp_path / "performance_plan_edited_01.json").write_text("{}", encoding="utf-8")
    edited_02 = copy.deepcopy(edited_01); edited_02["initial_states"]["ALICE"]["gaze"] = "GAZE-RIGHT"
    assert is_v2_plan_changed_from_loaded_snapshot(edited_02, active)
    assert default_edited_path(original_path).name == "performance_plan_edited_02.json"
    assert not is_v2_plan_changed_from_loaded_snapshot(edited_02, edited_02)


def test_real_v2_builder_baseline_is_canonical_and_untouched_score_is_not_edited():
    from expregaze_jali.dual_performance_plan_v2 import build_dual_performance_plan_v2
    from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model
    from tools.maya.dual_sparse_score_model import DualSparseScoreModel

    anchors = build_conversation_anchor_model("ALICE: Hello.\nBOB: No.", character_a="ALICE", character_b="BOB")
    proposal = {"analyze": "Context.", "initial_states": {"ALICE": {"affect": "Watchful-60", "gaze": "GAZE-BOB"}, "BOB": {"affect": "Neutral-60", "gaze": "GAZE-ALICE"}}, "initial_reasons": {"ALICE": "Ready.", "BOB": "Ready."}, "events": [{"event_id": "E1", "actor": "ALICE", "anchor_id": "w0002", "changes": {"gaze": "GAZE-DOWN", "head": "HEAD-DOWN-SUBTLE"}, "reason": "Withdraws."}], "diagnostics": {"errors": [], "warnings": []}}
    plan = build_dual_performance_plan_v2(proposal, anchor_model=anchors, sequence_id="fixture")
    assert plan["provenance"]["original_authored_content"] == canonical_v2_authored_content(plan)
    model = DualSparseScoreModel(plan, anchors)
    untouched = model.apply(dict(model.score_texts))
    assert not is_v2_plan_edited(untouched, model.original_plan)
    assert len(untouched["tracks"]["ALICE"]) == 1
    assert untouched["tracks"]["ALICE"][0]["changes"] == {"gaze": "GAZE-DOWN", "head": "HEAD-DOWN-SUBTLE"}
    texts = dict(model.score_texts)
    texts["ALICE"] = texts["ALICE"].replace("<GAZE-DOWN>", "<GAZE-RIGHT>")
    assert is_v2_plan_edited(model.apply(texts), model.original_plan)


def test_v2_delete_only_edit_saves_numbered_snapshot_without_resurrecting_event(tmp_path: Path):
    from tools.maya.dual_sparse_score_model import DualSparseScoreModel
    from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model

    anchors = build_conversation_anchor_model("ALICE: Hello.\nBOB: No.", character_a="ALICE", character_b="BOB")
    original = {"schema_version": "dual_performance_plan_v2", "characters": ["ALICE", "BOB"], "initial_states": {"ALICE": {"affect": "Watchful-60", "gaze": "GAZE-BOB"}, "BOB": {"affect": "Neutral-60", "gaze": "GAZE-ALICE"}}, "initial_reasons": {"ALICE": "Ready.", "BOB": "Ready."}, "tracks": {"ALICE": [{"event_id": "E1", "actor": "ALICE", "anchor_id": "w0001", "changes": {"gaze": "GAZE-DOWN"}, "reason": "Looks down."}], "BOB": []}}
    original_path = tmp_path / "performance_plan.json"
    save_performance_plan(original, original_path)
    model = DualSparseScoreModel(original, anchors)
    texts = dict(model.score_texts)
    texts["ALICE"] = texts["ALICE"].replace("<GAZE-DOWN>", "")
    current = model.apply(texts)
    assert current["tracks"]["ALICE"] == []
    assert is_v2_plan_edited(current, model.original_plan)
    snapshot = default_edited_path(original_path)
    save_animation_runtime_plan(model, texts, snapshot)
    assert json.loads(original_path.read_text(encoding="utf-8"))["tracks"]["ALICE"][0]["event_id"] == "E1"
    assert json.loads(snapshot.read_text(encoding="utf-8"))["tracks"]["ALICE"] == []
