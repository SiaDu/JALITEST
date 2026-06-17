from __future__ import annotations

import os
import sys


REPO_ROOT = os.environ.get(
    "JALITEST_REPO_ROOT",
    r"\\wsl.localhost\Ubuntu-24.04\home\sia\JaliTest",
)

CONFIG_PATH = os.path.join(
    REPO_ROOT,
    "configs",
    "maya",
    "valleygirl.yaml",
)

SRC_PATH = os.path.join(REPO_ROOT, "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from expregaze_jali.maya_apply_jali_annotation import apply_jali_annotation_from_config


apply_jali_annotation_from_config(CONFIG_PATH)