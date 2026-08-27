from pathlib import Path
import sys

MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
sys.path.insert(0, str(MAYA_TOOLS))
from dual_gaze_calibration import calibration_key, display_target, required_calibration_pairs  # noqa: E402


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
