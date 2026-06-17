from __future__ import annotations

from pathlib import Path

from expregaze_jali.maya_apply_gaze import (
    clamp_position,
    load_maya_gaze_config,
    resolve_maya_project_path,
    resolve_offset_position,
    resolve_repo_path,
    resolve_target_alias,
    resolve_target_position,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/maya/valleygirl.yaml"
TEST_BASE_POSITION = [0.0, 0.0, 126.0]
TEST_SAFE_BOUNDS = {
    "x": [-50.0, 50.0],
    "y": [-30.0, 30.0],
    "z": [126.0, 126.0],
}
TEST_DIRECTION_OFFSET_BOUNDS = {
    "x": [-50.0, 50.0],
    "y": [-30.0, 30.0],
    "z": [0.0, 0.0],
}


def test_load_config_and_aliases():
    config = load_maya_gaze_config(CONFIG)

    assert config["gaze_events_path"].startswith("data/processed/")
    assert config["maya_project_root"] == "E:/maya_project/JALI_test"
    assert resolve_target_alias("LISTENER", config["target_aliases"]) == "AIM_listener"
    assert resolve_target_alias("CRYSTAL", config["target_aliases"]) == "AIM_crystal"
    assert resolve_target_alias("DOWN", config["target_aliases"]) == "DOWN"


def test_offset_resolution_keeps_eye_stare_z_base():
    config = load_maya_gaze_config(CONFIG)

    assert resolve_offset_position(TEST_BASE_POSITION, config["direction_offsets"]["UP_RIGHT"]) == [
        40.0,
        25.0,
        126.0,
    ]
    assert resolve_target_position(
        target="UP_RIGHT",
        target_map=config["targets"],
        base_position=TEST_BASE_POSITION,
        direction_offsets=config["direction_offsets"],
        target_aliases=config["target_aliases"],
        direction_offset_bounds=TEST_DIRECTION_OFFSET_BOUNDS,
    ) == [40.0, 25.0, 126.0]


def test_safe_clamp_for_xy_and_fixed_z():
    config = load_maya_gaze_config(CONFIG)

    assert clamp_position([80.0, -80.0, 300.0], TEST_SAFE_BOUNDS) == [50.0, -30.0, 126.0]
    assert resolve_target_position(
        target="AIM_manual",
        target_map={"AIM_manual": {"position": [90.0, 40.0, 10.0]}},
        base_position=TEST_BASE_POSITION,
    ) == [90.0, 40.0, 10.0]


def test_config_path_resolution_and_windows_runner_defaults():
    config = load_maya_gaze_config(CONFIG)
    runner_text = (ROOT / "tools/maya/run_apply_gaze_events.py").read_text(encoding="utf-8")

    assert "C:\\Users\\sia\\JaliTest" not in runner_text
    assert "\\\\wsl.localhost\\Ubuntu-24.04\\home\\sia\\JaliTest" in runner_text
    assert resolve_repo_path(config["gaze_events_path"], config).endswith(
        "data/processed/gaze_script/Jali_proto_candidate_001_ProfessorCrystal__gaze_events_resolved.json"
    )
    assert resolve_maya_project_path("scenes/sounds_proto1/example.Textgrid", config).replace("\\", "/") == (
        "E:/maya_project/JALI_test/scenes/sounds_proto1/example.Textgrid"
    )
