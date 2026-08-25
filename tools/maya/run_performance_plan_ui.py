"""Maya Script Editor launcher for the Performance Plan Editor.

Paste this into Maya's Python tab, or run this file directly:

    exec(open(r"C:\\Users\\xyang\\Desktop\\Project\\JALITEST\\tools\\maya\\run_performance_plan_ui.py").read())

Set JALITEST_REPO_ROOT when the repository lives elsewhere. The launcher loads
the UI file directly, avoiding imports from the Python 3.12 backend package.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


REPO_ROOT = Path(
    os.environ.get("JALITEST_REPO_ROOT", r"C:\Users\xyang\Desktop\Project\JALITEST")
)
TOOLS_DIR = REPO_ROOT / "tools" / "maya"
UI_PATH = TOOLS_DIR / "performance_plan_ui.py"
_LOCAL_MODULES = (
    "performance_plan_ui_data",
    "performance_score_model",
    "authoring_session_data",
    "authoring_requirements",
    "backend_process_runner",
    "animation_apply_runner",
)

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def launch() -> object:
    if not UI_PATH.exists():
        raise FileNotFoundError(f"Performance Plan UI not found: {UI_PATH}")
    # Maya keeps Python modules alive between Script Editor executions. Drop
    # only this tool's helper modules so a newly saved UI and data helper load
    # together instead of mixing a current UI with a stale helper API.
    tools_root = TOOLS_DIR.resolve()
    for name in _LOCAL_MODULES:
        cached = sys.modules.get(name)
        cached_path = getattr(cached, "__file__", None)
        if cached_path and Path(cached_path).resolve().parent == tools_root:
            sys.modules.pop(name, None)
    module_name = "jalitest_performance_plan_ui"
    spec = importlib.util.spec_from_file_location(module_name, UI_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load Performance Plan UI: {UI_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.show_performance_plan_editor()


launch()
