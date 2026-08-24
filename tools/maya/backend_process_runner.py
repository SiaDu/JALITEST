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


@dataclass(frozen=True)
class AnimationCommand:
    program: str
    arguments: tuple[str, ...]
    repo_root: Path
    performance_plan: Path
    script_file: Path
    audio_folder: Path
    output_dir: Path
    manifest: Path


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


def prepare_animation_command(
    *,
    performance_plan: str | Path,
    script: str,
    audio_folder: str | Path,
    output_dir: str | Path,
    fps: float,
    repo_root: str | Path | None = None,
    backend_python: str | Path | None = None,
) -> AnimationCommand:
    root = resolve_repo_root(repo_root)
    plan_path = Path(performance_plan).resolve()
    if not plan_path.is_file():
        raise FileNotFoundError(f"Performance Plan not found: {plan_path}")
    if not str(script).strip():
        raise ValueError("Input Script is required for animation compilation.")
    audio_path = Path(audio_folder).resolve()
    if not audio_path.is_dir():
        raise FileNotFoundError(f"Input Audio Folder does not exist: {audio_path}")
    if float(fps) <= 0:
        raise ValueError("Maya scene FPS must be positive.")

    animation_dir = Path(output_dir).resolve()
    animation_dir.mkdir(parents=True, exist_ok=True)
    script_file = animation_dir / "input_script.txt"
    script_file.write_text(str(script), encoding="utf-8")
    arguments = (
        "-m",
        "expregaze_jali.compile_performance_plan",
        "--performance-plan",
        str(plan_path),
        "--script-file",
        str(script_file),
        "--audio-folder",
        str(audio_path),
        "--output-dir",
        str(animation_dir),
        "--fps",
        format(float(fps), ".12g"),
        "--overwrite",
    )
    return AnimationCommand(
        program=str(resolve_backend_python(root, backend_python)),
        arguments=arguments,
        repo_root=root,
        performance_plan=plan_path,
        script_file=script_file,
        audio_folder=audio_path,
        output_dir=animation_dir,
        manifest=animation_dir / "animation_manifest.json",
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


    class AnimationProcessRunner(QtCore.QObject):
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
            self.backend_python = resolve_backend_python(self.repo_root, backend_python)
            self.process: Any = None
            self.command: AnimationCommand | None = None
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
            self,
            *,
            performance_plan: str | Path,
            script: str,
            audio_folder: str | Path,
            output_dir: str | Path,
            fps: float,
        ) -> AnimationCommand:
            if self.running:
                raise RuntimeError("Animation compilation is already running.")
            self.command = prepare_animation_command(
                performance_plan=performance_plan,
                script=script,
                audio_folder=audio_folder,
                output_dir=output_dir,
                fps=fps,
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
            self._report_failure(f"Could not start or run the animation backend: {detail}")

        def _finished(self, exit_code: int, _exit_status: Any) -> None:
            self._read_stdout()
            self._read_stderr()
            if self._reported_failure:
                return
            if self.command is None:
                self._report_failure("Backend finished without an animation command.")
                return
            if exit_code == 0 and self.command.manifest.exists():
                self.succeeded.emit(self.command.manifest)
                return
            detail = self.stderr.strip() or self.stdout.strip()
            self._report_failure(detail or f"Animation backend exited with code {exit_code}.")

else:

    class BackendProcessRunner:  # pragma: no cover - only instantiated inside Maya
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("PySide6 is required to run the Maya backend process bridge.")


    class AnimationProcessRunner:  # pragma: no cover - only instantiated inside Maya
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("PySide6 is required to run the Maya animation process bridge.")
