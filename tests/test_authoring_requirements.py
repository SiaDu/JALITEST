from __future__ import annotations

from pathlib import Path
import sys


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from authoring_requirements import (  # noqa: E402
    animation_setup_issues,
    refresh_look_at_mappings,
    required_look_at_targets,
)


def _plan() -> dict:
    return {"events": [{"gaze": [
        {"target": "OBJECT_CRYSTAL"}, {"target": "OBJECT_DOOR"},
        {"target": "CHARACTER_WILL"}, {"target": "DOWN"},
        {"target": "OBJECT_CRYSTAL"}, {"target": "NONE"},
    ]}]}


def test_required_targets_extract_semantic_values_once_without_directions():
    assert required_look_at_targets(_plan()) == ["CRYSTAL", "DOOR", "WILL"]


def test_unresolved_alias_target_is_shown_in_animation_setup():
    assert required_look_at_targets({"events": [{"gaze": [{"value": "GAZE-B"}]}]}) == ["B"]


def test_required_target_refresh_preserves_existing_mapping_and_adds_empty_row():
    rows = refresh_look_at_mappings(
        ["CRYSTAL", "DOOR"], [{"semantic_target": "CRYSTAL", "maya_node": "|crystal_LOC"}]
    )
    assert rows == [
        {"semantic_target": "CRYSTAL", "maya_node": "|crystal_LOC"},
        {"semantic_target": "DOOR", "maya_node": ""},
    ]


def test_animation_preflight_reports_mapping_requirements_without_generation(tmp_path: Path):
    issues = animation_setup_issues(
        plan=_plan(), audio_folder=str(tmp_path / "missing"),
        characters=[{"script_name": "CHAYTON", "maya_node": ""}],
        look_at_mappings=[], node_exists=lambda _: False,
    )
    assert any("Audio:" in issue for issue in issues)
    assert "Character: CHAYTON: Maya rig/node not selected" in issues
    assert "Look-at targets: CRYSTAL: Maya node not selected" in issues
