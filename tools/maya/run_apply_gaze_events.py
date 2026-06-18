from __future__ import annotations

import importlib
import os
import sys


REPO_ROOT = os.environ.get(
    "JALITEST_REPO_ROOT",
    r"\\wsl.localhost\Ubuntu-24.04\home\sia\JaliTest",
)

MAYA_CONFIG_PATH = os.environ.get(
    "JALITEST_MAYA_CONFIG",
    os.path.join(REPO_ROOT, "configs", "maya", "valleygirl.yaml"),
)

SEQUENCE_CONFIG_PATH = os.environ.get(
    "JALITEST_SEQUENCE_CONFIG",
    os.path.join(REPO_ROOT, "configs", "sequences", "s038_1talk.yaml"),
)

PROJECT_CONFIG_PATH = os.environ.get(
    "JALITEST_PROJECT_CONFIG",
    os.path.join(REPO_ROOT, "configs", "project.yaml"),
)

SRC_PATH = os.path.join(REPO_ROOT, "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

# Maya keeps imported modules cached across Script Editor runs. Always reload so
# newly patched code on the WSL filesystem is actually used in the same Maya session.
import expregaze_jali.maya_apply_gaze as maya_apply_gaze

maya_apply_gaze = importlib.reload(maya_apply_gaze)

maya_apply_gaze.apply_gaze_events_from_config(
    MAYA_CONFIG_PATH,
    sequence_config_path=SEQUENCE_CONFIG_PATH,
    project_config_path=PROJECT_CONFIG_PATH,
)
