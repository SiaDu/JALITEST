from __future__ import annotations

from pathlib import Path
import sys


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from authoring_session_data import (  # noqa: E402
    build_authoring_session,
    default_authoring_session_path,
    load_authoring_session,
    save_authoring_session,
)


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
