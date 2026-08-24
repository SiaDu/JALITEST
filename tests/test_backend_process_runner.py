from __future__ import annotations

from pathlib import Path
import sys


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from backend_process_runner import (  # noqa: E402
    prepare_generation_command,
    resolve_backend_python,
)


def test_maya_generation_command_needs_no_sequence_environment(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("JALITEST_SEQUENCE_ID", raising=False)
    command = prepare_generation_command(
        script="AUNT EM: Stop threatening Dorothy.",
        context=None,
        target_character="AUNT_EM",
        repo_root=tmp_path,
        backend_python=tmp_path / ".venv" / "Scripts" / "python.exe",
        run_id="run_test",
    )
    arguments = list(command.arguments)
    assert arguments[:2] == ["-m", "expregaze_jali.generate_performance_plan"]
    assert "--sequence-id" not in arguments
    assert "--sequence-config" not in arguments
    assert "--context-file" not in arguments
    assert "JALITEST_SEQUENCE_ID" not in " ".join(arguments)
    assert command.script_file.read_text(encoding="utf-8").startswith("AUNT EM")
    assert command.performance_plan == command.run_dir / "performance_plan.json"


def test_maya_generation_command_uses_utf8_context_file(tmp_path: Path):
    context = "Aunt Em is normally restrained. 她现在失去耐心。"
    command = prepare_generation_command(
        script="AUNT EM: Enough!",
        context=context,
        target_character="AUNT_EM",
        repo_root=tmp_path,
        backend_python="backend-python.exe",
        run_id="run_context",
    )
    assert command.context_file is not None
    assert command.context_file.read_text(encoding="utf-8") == context
    assert "--context-file" in command.arguments
    for forbidden in ("movie_id", "shot_range", "local_window", "full_context"):
        assert forbidden not in " ".join(command.arguments).lower()


def test_backend_python_environment_override_and_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("JALITEST_BACKEND_PYTHON", "D:/backend/python.exe")
    assert resolve_backend_python(tmp_path).as_posix() == "D:/backend/python.exe"
    monkeypatch.delenv("JALITEST_BACKEND_PYTHON")
    assert resolve_backend_python(tmp_path) == tmp_path / ".venv" / "Scripts" / "python.exe"
