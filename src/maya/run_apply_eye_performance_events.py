from __future__ import annotations

import os
import sys


# Windows Maya reads the WSL repo through its UNC path. Override this env var
# if the distro name or checkout location changes.
REPO_ROOT = os.environ.get(
    "JALITEST_REPO_ROOT",
    r"\\wsl.localhost\Ubuntu-24.04\home\sia\JaliTest",
)
CONFIG_PATH = os.path.join(
    REPO_ROOT,
    "configs",
    "maya",
    "jali_proto_candidate_001_eye.yaml",
)

SRC_PATH = os.path.join(REPO_ROOT, "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from expregaze_jali.maya_apply_eye_performance import apply_eye_performance_events_from_config


apply_eye_performance_events_from_config(CONFIG_PATH)
