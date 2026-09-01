from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from authoring_session_data import (  # noqa: E402
    STUDY_UI_DIRECT_GENERATION,
    STUDY_UI_EDITABLE_PLAN,
    STUDY_UI_NORMAL,
    build_inspection_event,
    build_semantic_edit_event,
    build_study_ui_session,
    build_authoring_session,
    default_authoring_session_path,
    finish_study_ui_session,
    load_authoring_session,
    normalize_study_ui_mode,
    rebind_character_mappings,
    record_study_ui_mode_change,
    runtime_character_mappings,
    save_authoring_session,
    study_ui_section_state,
    validate_authoring_session,
)


def test_character_mapping_rebinds_rigs_by_identity_not_plan_row_order():
    previous = [
        {"script_name": "WILL", "maya_node": "Angela"},
        {"script_name": "AGNES", "maya_node": "ValleyGirl"},
    ]
    assert rebind_character_mappings(["AGNES", "WILL"], previous) == [
        {"script_name": "AGNES", "maya_node": "ValleyGirl"},
        {"script_name": "WILL", "maya_node": "Angela"},
    ]
    assert rebind_character_mappings(["MARTY", "DION"], previous) == [
        {"script_name": "MARTY", "maya_node": ""},
        {"script_name": "DION", "maya_node": ""},
    ]


def test_bug00_session_fixture_rebinds_its_actor_rigs_when_plan_order_swaps():
    fixture = Path(__file__).resolve().parents[1] / "data/processed/hci_runs/bug00"
    session = load_authoring_session(next(fixture.glob("*__authoring_session.json")))
    rebound = rebind_character_mappings(["WILL", "AGNES"], session["characters"])
    assert rebound == [
        {"script_name": "WILL", "maya_node": "|ValleyGirl_jRigMaya:JALI_GRP"},
        {"script_name": "AGNES", "maya_node": "|Angela_jRigMaya:JALI_GRP"},
    ]


def test_runtime_character_mapping_and_session_order_are_identity_keyed():
    persisted = [
        {"script_name": "WILL", "maya_node": "Angela"},
        {"script_name": "AGNES", "maya_node": "ValleyGirl"},
    ]
    restored = rebind_character_mappings(["AGNES", "WILL"], persisted)
    assert runtime_character_mappings(["WILL", "AGNES"], restored) == {
        "WILL": {"script_name": "WILL", "maya_node": "Angela"},
        "AGNES": {"script_name": "AGNES", "maya_node": "ValleyGirl"},
    }
    with pytest.raises(ValueError, match="Duplicate Character Mapping identity"):
        runtime_character_mappings(["WILL"], [{"script_name": "WILL", "maya_node": "Angela"}, {"script_name": "will", "maya_node": "Other"}])


def test_single_session_round_trip_preserves_mappings_audio_and_unknown_fields(tmp_path: Path):
    session = build_authoring_session(
        sequence_id="s029_1talk",
        mode="single",
        audio_folder="E:/audio/s029",
        input_script="AUNT EM: Enough!",
        input_context="Aunt Em is finally losing patience.",
        characters=[{"alias": "A", "script_name": "AUNT_EM", "maya_node": "|AuntEm_Main"}],
        look_at_targets=[{"semantic_target": "ALMIRA_GULCH", "maya_node": "|Gulch_look_locator"}],
        base={"future_setting": {"preserve": True}},
    )
    path = default_authoring_session_path(tmp_path / "s029__performance_plan.json", "s029_1talk")
    assert path == tmp_path / "s029_1talk__authoring_session.json"
    save_authoring_session(session, path)
    loaded = load_authoring_session(path)
    assert loaded == session
    assert loaded["audio_folder"] == "E:/audio/s029"
    assert loaded["input_script"] == "AUNT EM: Enough!"
    assert loaded["input_context"] == "Aunt Em is finally losing patience."
    assert loaded["characters"] == [
        {"alias": "A", "script_name": "AUNT_EM", "maya_node": "|AuntEm_Main"}
    ]
    assert loaded["look_at_targets"][0]["semantic_target"] == "ALMIRA_GULCH"
    assert loaded["future_setting"] == {"preserve": True}


def test_dual_session_has_ordered_a_b_character_mappings():
    session = build_authoring_session(
        sequence_id="scene",
        mode="dual",
        audio_folder="",
        input_script="PROFESSOR: Listen.\nDOROTHY: I am listening.",
        input_context="A two-character exchange.",
        characters=[
            {"alias": "A", "script_name": "PROFESSOR", "maya_node": "|Professor"},
            {"alias": "B", "script_name": "DOROTHY", "maya_node": "|Dorothy"},
        ],
        look_at_targets=[],
    )
    assert [row["alias"] for row in session["characters"]] == ["A", "B"]
    assert session["input_context"] == "A two-character exchange."


def test_session_data_is_separate_from_performance_plan():
    plan = {"schema_version": "performance_plan_v0", "sequence_id": "scene", "events": []}
    build_authoring_session(
        sequence_id="scene", mode="single", audio_folder="audio",
        input_script="ACTOR: Line.", input_context="Private UI context.", characters=[],
        look_at_targets=[{"semantic_target": "DOOR", "maya_node": "|Door"}],
    )
    assert "characters" not in plan
    assert "look_at_targets" not in plan
    assert "audio_folder" not in plan
    assert "input_script" not in plan
    assert "input_context" not in plan


def test_legacy_session_without_script_and_context_remains_valid(tmp_path: Path):
    path = tmp_path / "legacy_session.json"
    path.write_text(
        '{"schema_version":"authoring_session_v0","sequence_id":"scene",'
        '"mode":"single","audio_folder":"","characters":[],"look_at_targets":[]}',
        encoding="utf-8",
    )

    loaded = load_authoring_session(path)

    assert loaded.get("input_script", "") == ""
    assert loaded.get("input_context", "") == ""


def test_study_ui_modes_have_required_section_visibility_and_defaults():
    for mode in (STUDY_UI_NORMAL, STUDY_UI_EDITABLE_PLAN):
        assert study_ui_section_state(mode) == {
            "semantic": {"visible": True, "expanded": True},
            "interpretation": {"visible": True, "expanded": False},
        }
    assert study_ui_section_state(STUDY_UI_DIRECT_GENERATION) == {
        "semantic": {"visible": False, "expanded": True},
        "interpretation": {"visible": False, "expanded": False},
    }


def test_study_ui_mode_switch_is_presentation_only_and_does_not_modify_plan():
    plan = {
        "schema_version": "dual_performance_plan_v2",
        "characters": ["ALICE", "BOB"],
        "initial_states": {"ALICE": {}, "BOB": {}},
        "tracks": {"ALICE": [], "BOB": []},
    }
    before = copy.deepcopy(plan)
    study_ui_section_state(STUDY_UI_EDITABLE_PLAN)
    study_ui_section_state(STUDY_UI_DIRECT_GENERATION)
    assert plan == before
    assert normalize_study_ui_mode("EDITABLE_PLAN") == STUDY_UI_EDITABLE_PLAN


def test_inspection_events_are_distinct_from_semantic_edits_and_keep_context():
    events = [
        build_inspection_event(
            "interpretation_section_opened",
            study_ui_mode=STUDY_UI_EDITABLE_PLAN,
            timestamp="2026-08-31T12:00:00+00:00",
            sequence_id="SeqT",
            run_id="run_20260830_183422_293410",
            actor="WILL",
            event_id="E004",
        ),
        build_inspection_event(
            "interpretation_section_closed",
            study_ui_mode=STUDY_UI_EDITABLE_PLAN,
            timestamp="2026-08-31T12:00:05+00:00",
            sequence_id="SeqT",
        ),
    ]
    assert [event["event"] for event in events] == [
        "interpretation_section_opened",
        "interpretation_section_closed",
    ]
    assert events[0]["actor"] == "WILL"
    assert events[0]["event_id"] == "E004"
    assert all(event["event_type"] == "inspection" for event in events)
    assert all("semantic_edit" not in event["event"] for event in events)


def test_inspection_events_round_trip_in_authoring_sidecar(tmp_path: Path):
    event = build_inspection_event(
        "semantic_section_closed",
        study_ui_mode=STUDY_UI_EDITABLE_PLAN,
        timestamp="2026-08-31T12:00:00+00:00",
        sequence_id="scene",
    )
    semantic_edit = build_semantic_edit_event(
        study_ui_mode=STUDY_UI_EDITABLE_PLAN,
        timestamp="2026-08-31T12:00:05+00:00",
        sequence_id="scene",
    )
    study_ui_session = build_study_ui_session(
        STUDY_UI_EDITABLE_PLAN,
        timestamp="2026-08-31T11:59:59+00:00",
    )
    session = build_authoring_session(
        sequence_id="scene",
        mode="single",
        audio_folder="",
        characters=[],
        look_at_targets=[],
        base={
            "inspection_events": [event],
            "semantic_edit_events": [semantic_edit],
            "study_ui_sessions": [study_ui_session],
        },
    )
    path = tmp_path / "session.json"
    save_authoring_session(session, path)
    loaded = load_authoring_session(path)
    assert loaded["inspection_events"] == [event]
    assert loaded["semantic_edit_events"] == [semantic_edit]
    assert loaded["study_ui_sessions"] == [study_ui_session]


def test_jali_speech_base_runtime_metadata_round_trips_without_semantic_events(tmp_path: Path):
    speech_bases = {
        "AGNES": {
            "script_name": "AGNES", "maya_node": "|AGNES|JALI_GRP",
            "jsync": "|AGNES|JALI_GRP|speech|jSync17", "sound_file": "SeqT_AGNES",
            "wav_path": "C:/audio/SeqT_AGNES.wav", "txt_path": "C:/audio/SeqT_AGNES.txt",
            "txt_sha256": "a" * 64, "preparation_status": "prepared",
            "prepared_at": "2026-08-31T00:00:00+00:00",
        }
    }
    session = build_authoring_session(
        sequence_id="scene", mode="dual", audio_folder="C:/audio",
        characters=[
            {"alias": "A", "script_name": "AGNES", "maya_node": "|AGNES|JALI_GRP"},
            {"alias": "B", "script_name": "WILL", "maya_node": "|WILL|JALI_GRP"},
        ],
        look_at_targets=[],
        base={"jali_speech_bases": speech_bases, "semantic_edit_events": []},
    )
    path = tmp_path / "session.json"
    save_authoring_session(session, path)
    loaded = load_authoring_session(path)
    assert loaded["jali_speech_bases"] == speech_bases
    assert loaded["semantic_edit_events"] == []


def test_optional_jali_settings_round_trip_and_legacy_sessions_remain_valid(tmp_path: Path):
    settings = {
        "filter_silence_gaps": True,
        "silence_threshold_db": -60.0,
        "animate_from_scratch_next": True,
    }
    session = build_authoring_session(
        sequence_id="Seq3", mode="dual", audio_folder="C:/audio/Seq3",
        characters=[
            {"alias": "A", "script_name": "DION", "maya_node": "DION:JALI_GRP"},
            {"alias": "B", "script_name": "MARTY", "maya_node": "MARTY:JALI_GRP"},
        ],
        look_at_targets=[], base={"jali_speech_settings": settings},
    )
    assert session["jali_speech_settings"] == settings
    legacy = dict(session)
    legacy.pop("jali_speech_settings")
    validate_authoring_session(legacy)


def test_separate_event_streams_support_before_first_semantic_edit_metric():
    interpretation_open = build_inspection_event(
        "interpretation_section_opened",
        study_ui_mode=STUDY_UI_EDITABLE_PLAN,
        timestamp="2026-08-31T12:00:01+00:00",
        sequence_id="scene",
    )
    first_edit = build_semantic_edit_event(
        study_ui_mode=STUDY_UI_EDITABLE_PLAN,
        timestamp="2026-08-31T12:00:02+00:00",
        sequence_id="scene",
    )
    assert interpretation_open["event_type"] == "inspection"
    assert first_edit["event_type"] == "semantic_edit"
    assert interpretation_open["timestamp"] < first_edit["timestamp"]


def test_lifecycle_metadata_supports_initial_open_time_without_fake_inspection():
    ui_session = build_study_ui_session(
        STUDY_UI_EDITABLE_PLAN,
        timestamp="2026-08-31T12:00:00+00:00",
    )
    assert ui_session["initial_section_state"]["semantic"] == {
        "visible": True,
        "expanded": True,
    }
    assert ui_session["initial_section_state"]["interpretation"]["expanded"] is False
    assert "inspection_events" not in ui_session

    record_study_ui_mode_change(
        ui_session,
        STUDY_UI_DIRECT_GENERATION,
        timestamp="2026-08-31T12:00:03+00:00",
    )
    finish_study_ui_session(
        ui_session,
        timestamp="2026-08-31T12:00:05+00:00",
    )

    assert ui_session["mode_changes"] == [
        {
            "timestamp": "2026-08-31T12:00:03+00:00",
            "study_ui_mode": STUDY_UI_DIRECT_GENERATION,
            "section_state": study_ui_section_state(STUDY_UI_DIRECT_GENERATION),
        }
    ]
    assert ui_session["ended_at"] == "2026-08-31T12:00:05+00:00"
