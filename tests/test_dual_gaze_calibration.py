from pathlib import Path
import sys

MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
sys.path.insert(0, str(MAYA_TOOLS))
from dual_gaze_calibration import capture_target_pose_and_restore, calibration_key, display_target, dual_actor_row_index, optional_look_at_validation_error, required_calibration_pairs  # noqa: E402


def test_optional_look_at_requires_a_physical_named_target():
    assert optional_look_at_validation_error("", "LETTER")
    assert optional_look_at_validation_error("ALICE", "")
    for direction in ("UP", "DOWN", "LEFT", "RIGHT", "UP_LEFT", "UP_RIGHT", "DOWN_LEFT", "DOWN_RIGHT"):
        assert optional_look_at_validation_error("ALICE", direction) == "Directional gaze targets do not require Look-at calibration."
    assert optional_look_at_validation_error("ALICE", "NONE") == "NONE is not a physical Look-at calibration target."
    assert optional_look_at_validation_error("ALICE", "letter") is None


def test_calibration_pairs_are_actor_specific_and_display_real_names():
    plan = {"characters": {"A": "AGNES", "B": "WILL"}, "phrases": [
        {"states": {"A": {"gaze": "GAZE-B"}, "B": {"gaze": "GLANCE-A"}}},
        {"states": {"A": {"gaze": "AVERT-DOWN"}, "B": {"gaze": "NONE"}}},
    ]}
    assert required_calibration_pairs(plan) == [("A", "B"), ("B", "A")]
    assert calibration_key("A", "B") != calibration_key("B", "A")
    assert display_target("B", plan["characters"]) == "WILL"
    assert display_target("OBJECT_HAWK", plan["characters"]) == "HAWK"


def test_object_targets_are_independent_calibration_pairs():
    plan = {"characters": {"A": "AGNES", "B": "WILL"}, "phrases": [
        {"states": {"A": {"gaze": "GAZE-OBJECT_HAWK"}, "B": {"gaze": "NONE"}}},
    ]}
    assert required_calibration_pairs(plan) == [("A", "OBJECT_HAWK")]


def test_v1_names_stay_name_keyed_for_calibration_display_and_rows():
    plan = {"characters": ["ALICE", "BOB"], "phrases": [
        {"states": {"ALICE": {"gaze": "GAZE-BOB"}, "BOB": {"gaze": "GAZE-OBJECT_HAWK"}}},
    ]}
    assert required_calibration_pairs(plan) == [("ALICE", "BOB"), ("BOB", "OBJECT_HAWK")]
    assert display_target("ALICE", plan["characters"]) == "ALICE"
    assert display_target("OBJECT_HAWK", plan["characters"]) == "HAWK"
    assert dual_actor_row_index(plan, "ALICE") == 0
    assert dual_actor_row_index(plan, "BOB") == 1


def test_v2_calibration_pairs_include_initial_and_sparse_named_gazes_only():
    plan = {
        "schema_version": "dual_performance_plan_v2", "characters": ["JOAN", "CHAYTON"],
        "initial_states": {"JOAN": {"gaze": "GAZE-CHAYTON"}, "CHAYTON": {"gaze": "GAZE-DOWN"}},
        "tracks": {
            "JOAN": [
                {"changes": {"gaze": "GLANCE-IN_SIDE_ROOM"}},
                {"changes": {"gaze": "GAZE-UP_LEFT"}},
                {"changes": {"gaze": "GAZE-CHAYTON"}},
            ],
            "CHAYTON": [{"changes": {"gaze": "GAZE-UP"}}],
        },
    }
    assert required_calibration_pairs(plan) == [("JOAN", "CHAYTON"), ("JOAN", "IN_SIDE_ROOM")]


def test_capture_look_at_keeps_local_pose_as_data_and_restores_forward_neutral():
    class Cmds:
        values = {"eye.translateX": 4.0, "eye.translateY": -3.0, "eye.translateZ": 12.0}
        writes: list[tuple[str, float]] = []
        def getAttr(self, plug): return self.values[plug]
        def xform(self, *_args, **_kwargs): return [100.0, 200.0, 300.0]
        def setAttr(self, plug, value): self.writes.append((plug, value))
    cmds = Cmds()
    captured = capture_target_pose_and_restore("eye", "eyes", baseline_translate_z=7.0, both_eyes_translate=[1.5, -2.5], cmds_module=cmds)
    assert captured == {"eye_stare_translate": [4.0, -3.0, 12.0], "eye_stare_world_position": [100.0, 200.0, 300.0]}
    assert cmds.writes == [("eye.translateX", 0.0), ("eye.translateY", 0.0), ("eye.translateZ", 7.0), ("eyes.translateX", 0.0), ("eyes.translateY", 0.0)]
