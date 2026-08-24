"""Non-blocking Maya-to-backend bridge for HCI Performance Plan generation.

Command preparation is pure Python and testable without Maya or PySide6.  The
QProcess wrapper is defined when PySide6 is available in Maya 2025.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from typing import Any


try:
    from PySide6 import QtCore
except ImportError:  # pragma: no cover - normal non-Maya test environment
    QtCore = None  # type: ignore[assignment]


@dataclass(frozen=True)
class BackendCommand:
    program: str
    arguments: tuple[str, ...]
    repo_root: Path
    run_id: str
    run_dir: Path
    script_file: Path
    context_file: Path | None
    performance_plan: Path


def resolve_repo_root(value: str | Path | None = None) -> Path:
    if value is not None:
        return Path(value).resolve()
    configured = os.environ.get("JALITEST_REPO_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[2]


def resolve_backend_python(
    repo_root: str | Path, value: str | Path | None = None
) -> Path:
    configured = value or os.environ.get("JALITEST_BACKEND_PYTHON")
    if configured:
        return Path(configured)
    return Path(repo_root) / ".venv" / "Scripts" / "python.exe"


def generate_backend_run_id(now: datetime | None = None) -> str:
    instant = now or datetime.now()
    return instant.strftime("run_%Y%m%d_%H%M%S_%f")


def prepare_generation_command(
    *,
    script: str,
    context: str | None,
    target_character: str | None,
    repo_root: str | Path | None = None,
    backend_python: str | Path | None = None,
    run_id: str | None = None,
) -> BackendCommand:
    clean_script = str(script)
    if not clean_script.strip():
        raise ValueError("Input Script is required.")
    clean_character = str(target_character or "").strip()
    if not clean_character:
        raise ValueError("A script character is required in Character Mapping.")

    root = resolve_repo_root(repo_root)
    resolved_run_id = run_id or generate_backend_run_id()
    run_dir = root / "data" / "processed" / "hci_runs" / resolved_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    script_file = run_dir / "input_script.txt"
    script_file.write_text(clean_script, encoding="utf-8")

    clean_context = str(context or "").strip()
    context_file: Path | None = None
    if clean_context:
        context_file = run_dir / "input_context.txt"
        context_file.write_text(clean_context, encoding="utf-8")

    arguments = [
        "-m",
        "expregaze_jali.generate_performance_plan",
        "--script-file",
        str(script_file),
        "--target-character",
        clean_character,
        "--run-id",
        resolved_run_id,
        "--output-dir",
        str(run_dir),
        "--overwrite",
    ]
    if context_file is not None:
        arguments[4:4] = ["--context-file", str(context_file)]

    return BackendCommand(
        program=str(resolve_backend_python(root, backend_python)),
        arguments=tuple(arguments),
        repo_root=root,
        run_id=resolved_run_id,
        run_dir=run_dir,
        script_file=script_file,
        context_file=context_file,
        performance_plan=run_dir / "performance_plan.json",
    )


if QtCore is not None:

    class BackendProcessRunner(QtCore.QObject):
        output_received = QtCore.Signal(str)
        succeeded = QtCore.Signal(object)
        failed = QtCore.Signal(str)

        def __init__(
            self,
            parent: Any = None,
            *,
            repo_root: str | Path | None = None,
            backend_python: str | Path | None = None,
        ) -> None:
            super().__init__(parent)
            self.repo_root = resolve_repo_root(repo_root)
            self.backend_python = resolve_backend_python(
                self.repo_root, backend_python
            )
            self.process: Any = None
            self.command: BackendCommand | None = None
            self.stdout = ""
            self.stderr = ""
            self._reported_failure = False

        @property
        def running(self) -> bool:
            return bool(
                self.process is not None
                and self.process.state() != QtCore.QProcess.ProcessState.NotRunning
            )

        def start(
            self, *, script: str, context: str | None, target_character: str
        ) -> BackendCommand:
            if self.running:
                raise RuntimeError("Performance Plan generation is already running.")
            self.command = prepare_generation_command(
                script=script,
                context=context,
                target_character=target_character,
                repo_root=self.repo_root,
                backend_python=self.backend_python,
            )
            self.stdout = ""
            self.stderr = ""
            self._reported_failure = False
            self.process = QtCore.QProcess(self)
            self.process.setWorkingDirectory(str(self.command.repo_root))
            environment = QtCore.QProcessEnvironment.systemEnvironment()
            source_dir = str(self.command.repo_root / "src")
            existing_pythonpath = environment.value("PYTHONPATH")
            environment.insert(
                "PYTHONPATH",
                source_dir
                if not existing_pythonpath
                else source_dir + os.pathsep + existing_pythonpath,
            )
            self.process.setProcessEnvironment(environment)
            self.process.readyReadStandardOutput.connect(self._read_stdout)
            self.process.readyReadStandardError.connect(self._read_stderr)
            self.process.finished.connect(self._finished)
            self.process.errorOccurred.connect(self._process_error)
            self.process.start(self.command.program, list(self.command.arguments))
            return self.command

        def _read_stdout(self) -> None:
            text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
            if text:
                self.stdout += text
                self.output_received.emit(text.rstrip())

        def _read_stderr(self) -> None:
            text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
            if text:
                self.stderr += text
                self.output_received.emit("[stderr] " + text.rstrip())

        def _report_failure(self, message: str) -> None:
            if not self._reported_failure:
                self._reported_failure = True
                self.failed.emit(message)

        def _process_error(self, error: Any) -> None:
            detail = self.process.errorString() if self.process is not None else str(error)
            self._report_failure(f"Could not start or run the backend process: {detail}")

        def _finished(self, exit_code: int, _exit_status: Any) -> None:
            self._read_stdout()
            self._read_stderr()
            if self._reported_failure:
                return
            if self.command is None:
                self._report_failure("Backend process finished without a generation command.")
                return
            if exit_code == 0 and self.command.performance_plan.exists():
                self.succeeded.emit(self.command.performance_plan)
                return
            detail = self.stderr.strip() or self.stdout.strip()
            if not detail:
                detail = f"Backend process exited with code {exit_code}."
            self._report_failure(detail)

else:

    class BackendProcessRunner:  # pragma: no cover - only instantiated inside Maya
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("PySide6 is required to run the Maya backend process bridge.")
