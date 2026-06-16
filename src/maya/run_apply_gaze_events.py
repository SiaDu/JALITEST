from __future__ import annotations

import os
import sys


# Edit this path if the repo lives somewhere else on your Maya machine.
REPO_ROOT = os.environ.get("JALITEST_REPO_ROOT", r"C:\Users\sia\JaliTest")
CONFIG_PATH = os.path.join(REPO_ROOT, "configs", "maya", "jali_proto_candidate_001_gaze.yaml")

SRC_PATH = os.path.join(REPO_ROOT, "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from expregaze_jali.maya_apply_gaze import apply_gaze_events_from_config


apply_gaze_events_from_config(CONFIG_PATH)
