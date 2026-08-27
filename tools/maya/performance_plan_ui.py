"""Maya 2025 PySide6 editor for semantic Performance Plan JSON files.

This tool intentionally has no dependency on the Python 3.12 backend package.
It reads and writes only Performance Plan JSON through the adjacent data helper.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import sys
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import wrapInstance
from maya import OpenMayaUI as omui
from maya import cmds


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from performance_plan_ui_data import (  # noqa: E402
    default_edited_path,
    load_performance_plan,
    save_animation_runtime_plan,
    save_performance_plan,
    set_event_intent,
    set_event_locks,
    update_affect_span,
    update_blink_span,
    update_gaze_span,
    update_head_span,
    update_lid_state_span,
)
from performance_score_model import (  # noqa: E402
    DualPerformanceScoreModel,
    PerformanceScoreModel,
    format_rationale_view,
)
from authoring_session_data import (  # noqa: E402
    build_authoring_session,
    default_authoring_session_path,
    load_authoring_session,
    save_authoring_session,
)
from animation_apply_runner import (  # noqa: E402
    apply_animation_artifacts,
    apply_dual_listener_mask_artifacts,
    apply_dual_speaker_emotion_artifacts,
    current_scene_fps,
    resolve_jali_source_transcript_path,
    resolve_jsync_for_character,
    prepare_dual_listener_mask_artifacts,
)
from authoring_requirements import (  # noqa: E402
    animation_setup_issues,
    refresh_look_at_mappings,
    required_look_at_targets,
)
from dual_source_transcripts import export_dual_source_transcripts, resolve_character_wav  # noqa: E402
from backend_process_runner import AnimationProcessRunner, BackendProcessRunner  # noqa: E402


WINDOW_OBJECT_NAME = "jalitestPerformancePlanEditor"
PERFORMANCE_PLAN_EDITOR: "PerformancePlanEditor | None" = None


def maya_main_window() -> QtWidgets.QWidget:
    pointer = omui.MQtUtil.mainWindow()
    if pointer is None:
        raise RuntimeError("Could not find Maya's main window.")
    return wrapInstance(int(pointer), QtWidgets.QWidget)


def _readonly_item(value: Any) -> QtWidgets.QTableWidgetItem:
    item = QtWidgets.QTableWidgetItem("" if value is None else str(value))
    item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
    return item


def _editable_item(value: Any) -> QtWidgets.QTableWidgetItem:
    return QtWidgets.QTableWidgetItem("" if value is None else str(value))


def _configure_multiline_editor(
    editor: QtWidgets.QPlainTextEdit,
    *,
    height: int,
    read_only: bool = False,
    fixed_height: bool = False,
) -> None:
    """Apply the shared Semantic Performance Score text treatment."""
    editor.setFont(
        QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
    )
    editor.setReadOnly(read_only)
    if fixed_height:
        editor.setFixedHeight(height)
    else:
        editor.setMinimumHeight(height)


class PerformancePlanEditor(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent or maya_main_window())
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Performance Plan Editor")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(1280, 860)

        self.plan: dict[str, Any] | None = None
        self.score_model: PerformanceScoreModel | DualPerformanceScoreModel | None = None
        self.authoring_session: dict[str, Any] | None = None
        self.source_path: Path | None = None
        self.current_event_index: int | None = None
        self._building = False
        self.character_rows: list[tuple[QtWidgets.QLineEdit, QtWidgets.QLineEdit, QtWidgets.QWidget]] = []
        self.character_mapping_rows: list[QtWidgets.QWidget] = []
        self.look_at_rows: list[tuple[QtWidgets.QLineEdit, QtWidgets.QLineEdit, QtWidgets.QWidget]] = []
        self._pending_animation_mode = "single"
        self._pending_dual_mappings: dict[str, dict[str, str]] = {}

        self._build_ui()
        self.backend_runner = BackendProcessRunner(self)
        self.backend_runner.output_received.connect(self._append_backend_output)
        self.backend_runner.succeeded.connect(self._generation_succeeded)
        self.backend_runner.failed.connect(self._generation_failed)
        self.animation_runner = AnimationProcessRunner(self)
        self.animation_runner.output_received.connect(self._append_backend_output)
        self.animation_runner.succeeded.connect(self._animation_compile_succeeded)
        self.animation_runner.failed.connect(self._animation_failed)

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_authoring_tab()
        self._build_advanced_tab()

    def _build_authoring_tab(self) -> None:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        authoring = QtWidgets.QVBoxLayout(content)
        authoring.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self._build_setup(authoring)
        self._build_acting_interpretation(authoring)
        self._build_semantic_score(authoring)
        self._build_reason_view(authoring)
        self._build_animation_setup(authoring)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, "Authoring")

    def _build_animation_setup(self, authoring: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("ANIMATION SETUP")
        layout = QtWidgets.QVBoxLayout(group)
        layout.addWidget(QtWidgets.QLabel("Character Mapping"))
        for script_name, rig_name, row in self.character_rows:
            mapping_row = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(mapping_row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            script_display = QtWidgets.QLabel(script_name.text())
            script_name.textChanged.connect(script_display.setText)
            row_layout.addWidget(script_display, 1)
            row_layout.addWidget(QtWidgets.QLabel("->"))
            row_layout.addWidget(rig_name, 1)
            select = QtWidgets.QPushButton("Use Scene Selection")
            select.clicked.connect(lambda _checked=False, field=rig_name: self._use_scene_selection(field))
            row_layout.addWidget(select)
            layout.addWidget(mapping_row)
            self.character_mapping_rows.append(mapping_row)
        layout.addWidget(QtWidgets.QLabel("Required Look-at Targets"))
        self.look_at_layout = QtWidgets.QVBoxLayout()
        layout.addLayout(self.look_at_layout)
        bottom = QtWidgets.QHBoxLayout()
        self.generate_animation_button = QtWidgets.QPushButton("Generate Animation")
        self.generate_animation_button.clicked.connect(self.generate_animation)
        bottom.addWidget(self.generate_animation_button)
        self.animation_status = QtWidgets.QLabel("Ready.")
        bottom.addWidget(self.animation_status)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        authoring.addWidget(group)
        self._update_character_mode()

    def _build_setup(self, parent: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("SETUP")
        layout = QtWidgets.QVBoxLayout(group)
        layout.addWidget(QtWidgets.QLabel("Input Script"))
        self.input_script = QtWidgets.QPlainTextEdit()
        self.input_script.setPlaceholderText("Paste or load the dialogue/script used for the performance plan.")
        _configure_multiline_editor(self.input_script, height=240)
        layout.addWidget(self.input_script)

        layout.addWidget(QtWidgets.QLabel("Context (Optional)"))
        self.input_context = QtWidgets.QPlainTextEdit()
        self.input_context.setPlaceholderText(
            "Optional scene, story, character, or performance context."
        )
        _configure_multiline_editor(self.input_context, height=200)
        layout.addWidget(self.input_context)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Authoring Mode"))
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Single Character", "Dual Character"])
        self.mode_combo.currentIndexChanged.connect(self._update_character_mode)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        character_grid = QtWidgets.QGridLayout()
        character_grid.addWidget(QtWidgets.QLabel("Script Character"), 0, 0)
        for row_index in range(2):
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            script_name = QtWidgets.QLineEdit()
            script_name.setPlaceholderText("PROFESSOR" if row_index == 0 else "DOROTHY")
            rig_name = QtWidgets.QLineEdit()
            script_name.textChanged.connect(lambda name, field=rig_name: field.setPlaceholderText(f"Select {name or 'this character'}'s JALI_GRP"))
            rig_name.setPlaceholderText("Select this character's JALI_GRP")
            row_layout.addWidget(script_name, 1)
            row_layout.addWidget(QtWidgets.QLabel("→"))
            character_grid.addWidget(row_widget, row_index + 1, 0, 1, 4)
            self.character_rows.append((script_name, rig_name, row_widget))
        layout.addLayout(character_grid)

        audio = QtWidgets.QHBoxLayout()
        audio.addWidget(QtWidgets.QLabel("Input Audio Folder"))
        self.audio_folder = QtWidgets.QLineEdit()
        audio.addWidget(self.audio_folder, 1)
        choose_audio = QtWidgets.QPushButton("Select Folder")
        choose_audio.clicked.connect(self._select_audio_folder)
        audio.addWidget(choose_audio)
        layout.addLayout(audio)

        self.generate_plan_button = QtWidgets.QPushButton("Generate Performance Plan")
        self.generate_plan_button.clicked.connect(self.generate_performance_plan)
        layout.addWidget(self.generate_plan_button)
        self.generation_status = QtWidgets.QLabel("Ready.")
        layout.addWidget(self.generation_status)
        parent.addWidget(group)
        self._update_character_mode()

    def _build_acting_interpretation(self, parent: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("ACTING INTERPRETATION")
        layout = QtWidgets.QVBoxLayout(group)
        self.acting_interpretation = QtWidgets.QPlainTextEdit()
        self.acting_interpretation.setPlaceholderText(
            "Scene:\n\nAffective state:\n\nNarrative intent:"
        )
        _configure_multiline_editor(self.acting_interpretation, height=240)
        layout.addWidget(self.acting_interpretation)
        self.regenerate_button = QtWidgets.QPushButton("Regenerate Plan")
        self.regenerate_button.clicked.connect(lambda: self._show_phase_one_placeholder("Regenerate Plan"))
        layout.addWidget(self.regenerate_button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        parent.addWidget(group)

    def _build_semantic_score(self, parent: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("SEMANTIC PERFORMANCE SCORE")
        layout = QtWidgets.QVBoxLayout(group)
        self.score_editor = QtWidgets.QPlainTextEdit()
        self.score_editor.setPlaceholderText("Generate or load a performance plan to begin editing.")
        _configure_multiline_editor(self.score_editor, height=260)
        self.score_editor.textChanged.connect(self._score_changed)
        layout.addWidget(self.score_editor)
        controls = QtWidgets.QHBoxLayout()
        self.validate_score_button = QtWidgets.QPushButton("Validate Score")
        self.validate_score_button.clicked.connect(self.validate_score)
        controls.addWidget(self.validate_score_button)
        self.apply_score_button = QtWidgets.QPushButton("Apply Score Edits")
        self.apply_score_button.clicked.connect(self.apply_score_edits)
        controls.addWidget(self.apply_score_button)
        self.validation_label = QtWidgets.QLabel("No plan loaded")
        controls.addWidget(self.validation_label, 1)
        layout.addLayout(controls)
        self.validation_details = QtWidgets.QPlainTextEdit()
        _configure_multiline_editor(
            self.validation_details, height=120, read_only=True, fixed_height=True
        )
        self.validation_details.hide()
        layout.addWidget(self.validation_details)
        parent.addWidget(group)

    def _build_reason_view(self, parent: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("REASON BY PHRASE")
        layout = QtWidgets.QVBoxLayout(group)
        selector = QtWidgets.QHBoxLayout()
        selector.addWidget(QtWidgets.QLabel("Phrase:"))
        self.phrase_number = QtWidgets.QSpinBox()
        self.phrase_number.setRange(1, 1)
        self.phrase_number.valueChanged.connect(self._refresh_phrase_reason)
        selector.addWidget(self.phrase_number)
        selector.addStretch(1)
        layout.addLayout(selector)
        self.phrase_reason = QtWidgets.QPlainTextEdit()
        _configure_multiline_editor(self.phrase_reason, height=240, read_only=True)
        layout.addWidget(self.phrase_reason)
        parent.addWidget(group)

    def _build_advanced_tab(self) -> None:
        advanced = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(advanced)

        file_controls = QtWidgets.QHBoxLayout()
        self.load_button = QtWidgets.QPushButton("Load Existing Plan...")
        self.load_button.clicked.connect(self.load_plan_dialog)
        file_controls.addWidget(self.load_button)
        self.metadata_label = QtWidgets.QLabel("No plan loaded")
        self.metadata_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        file_controls.addWidget(self.metadata_label, 1)
        self.save_button = QtWidgets.QPushButton("Save Performance Plan")
        self.save_button.clicked.connect(self.save_edited_plan)
        file_controls.addWidget(self.save_button)
        self.save_as_button = QtWidgets.QPushButton("Save Performance Plan As...")
        self.save_as_button.clicked.connect(self.save_edited_plan_as)
        file_controls.addWidget(self.save_as_button)
        layout.addLayout(file_controls)

        layout.addWidget(QtWidgets.QLabel("Backend Generation Log"))
        self.backend_log = QtWidgets.QPlainTextEdit()
        _configure_multiline_editor(
            self.backend_log, height=180, read_only=True, fixed_height=True
        )
        self.backend_log.setMaximumBlockCount(500)
        layout.addWidget(self.backend_log)

        self.diagnostics = QtWidgets.QPlainTextEdit()
        _configure_multiline_editor(
            self.diagnostics, height=160, read_only=True, fixed_height=True
        )
        self.diagnostics.setMaximumBlockCount(200)
        layout.addWidget(self.diagnostics)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.event_list = QtWidgets.QListWidget()
        self.event_list.setMinimumWidth(260)
        self.event_list.currentRowChanged.connect(self._select_event)
        splitter.addWidget(self.event_list)

        self.right_content = QtWidgets.QWidget()
        self.right_layout = QtWidgets.QVBoxLayout(self.right_content)
        self.right_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self._build_event_metadata()

        self.visible_affect = self._create_table(
            "Visible Affect", ["State", "Intensity", "Start", "End", "Source Tag"]
        )
        self.hidden_affect = self._create_table(
            "Hidden Affect", ["State", "Intensity", "Start", "End", "Source Tag"]
        )
        self.gaze = self._create_table("Gaze", ["Mode", "Target", "Start", "End", "Source Tag"])
        self.head = self._create_table("Head", ["Involvement", "Start", "End", "Source Tag"])
        self.lid_state = self._create_table("Lid State", ["Lid State", "Start", "End", "Source Tag"])
        self.performative_blink = self._create_table(
            "Performative Blink", ["Value", "Start", "End", "Source Tag"]
        )
        self.blink_suppression = self._create_table(
            "Blink Suppression", ["Value", "Start", "End", "Source Tag"]
        )
        self._build_rationale()
        self._build_locks()

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.right_content)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.tabs.addTab(advanced, "Advanced / Debug")

    def _select_audio_folder(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Input Audio Folder", self.audio_folder.text() or str(Path.cwd())
        )
        if path:
            self.audio_folder.setText(path)

    def _use_scene_selection(self, field: QtWidgets.QLineEdit) -> None:
        selected = cmds.ls(selection=True, long=True) or []
        if not selected:
            QtWidgets.QMessageBox.information(self, "No Scene Selection", "Select a Maya node first.")
            return
        node = str(selected[0])
        if not (node.rsplit("|", 1)[-1].endswith("JALI_GRP") or cmds.objExists(node + "|FACSMaster")):
            QtWidgets.QMessageBox.warning(self, "Invalid Character Root", "Select this character's JALI_GRP (or an equivalent root containing FACSMaster).")
            return
        field.setText(node)

    def _add_look_at_target(self, semantic_name: str = "") -> None:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        semantic = QtWidgets.QLineEdit(str(semantic_name) if isinstance(semantic_name, str) else "")
        semantic.setPlaceholderText("CRYSTAL")
        maya_object = QtWidgets.QLineEdit()
        maya_object.setPlaceholderText("Select geometry or locator")
        select = QtWidgets.QPushButton("Use Scene Selection")
        select.clicked.connect(lambda _checked=False, field=maya_object: self._use_scene_selection(field))
        remove = QtWidgets.QPushButton("Remove")
        remove.clicked.connect(lambda: self._remove_look_at_target(row))
        layout.addWidget(semantic, 1)
        layout.addWidget(QtWidgets.QLabel("→"))
        layout.addWidget(maya_object, 1)
        layout.addWidget(select)
        layout.addWidget(remove)
        self.look_at_layout.addWidget(row)
        self.look_at_rows.append((semantic, maya_object, row))

    def _remove_look_at_target(self, row: QtWidgets.QWidget) -> None:
        self.look_at_rows = [item for item in self.look_at_rows if item[2] is not row]
        row.deleteLater()

    def _update_character_mode(self) -> None:
        if not self.character_rows:
            return
        dual = self.mode_combo.currentIndex() == 1
        self.character_rows[1][2].setVisible(dual)
        if self.character_mapping_rows:
            self.character_mapping_rows[1].setVisible(dual)
        if dual:
            self.validation_label.setText(
                "Dual semantic authoring uses one shared conversation plan."
            )

    def _show_phase_one_placeholder(self, action: str) -> None:
        QtWidgets.QMessageBox.information(
            self,
            f"{action} — Phase 1",
            f"{action} backend execution is deferred. The UI plumbing is present, but Maya does not call the backend or an LLM.",
        )

    def generate_performance_plan(self) -> None:
        script = self.input_script.toPlainText()
        dual = self.mode_combo.currentIndex() == 1
        character_a = self.character_rows[0][0].text().strip()
        character_b = self.character_rows[1][0].text().strip()
        if not script.strip():
            QtWidgets.QMessageBox.warning(
                self, "Input Script Required", "Enter the script before generating a performance plan."
            )
            return
        if not character_a or (dual and not character_b):
            QtWidgets.QMessageBox.warning(
                self,
                "Character Mapping Required",
                "Enter Script Character A and Script Character B before generating."
                if dual else
                "Enter Script Character A before generating.",
            )
            return
        if dual:
            try:
                names = [self.character_rows[index][0].text().strip() for index in (0, 1)]
                if not all(names) or names[0].casefold() == names[1].casefold():
                    raise ValueError("Dual mode requires two distinct Script Character names.")
                for name in names:
                    resolve_character_wav(self.audio_folder.text().strip(), name)
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "Dual Audio Setup Incomplete", str(exc))
                return
        self._pending_animation_mode = "single"
        self._pending_dual_mappings = {}
        self.backend_log.clear()
        self.generate_plan_button.setEnabled(False)
        self.generation_status.setText("Generating performance plan...")
        self.generation_status.setStyleSheet("color: #1d4ed8;")
        try:
            command = self.backend_runner.start(
                script=script,
                context=self.input_context.toPlainText(),
                mode="dual" if dual else "single",
                character_a=character_a,
                character_b=character_b if dual else None,
            )
        except Exception as exc:
            self._generation_failed(str(exc))
            return
        self._append_backend_output(f"Run ID: {command.run_id}")
        self._append_backend_output(f"Backend Python: {command.program}")
        self._append_backend_output(f"Output directory: {command.run_dir}")

    def _append_backend_output(self, value: str) -> None:
        text = str(value).strip()
        if text:
            self.backend_log.appendPlainText(text)

    def _generation_succeeded(self, plan_path: object) -> None:
        self.generate_plan_button.setEnabled(True)
        path = Path(str(plan_path))
        self.load_plan(path, preserve_authoring_text=True)
        if self.source_path == path:
            try:
                if self.mode_combo.currentIndex() == 1:
                    names = [self.character_rows[index][0].text().strip() for index in (0, 1)]
                    exports = export_dual_source_transcripts(script=self.input_script.toPlainText(), audio_folder=self.audio_folder.text().strip(), characters=names)
                    self._append_backend_output("JALI source transcripts:")
                    for name in names:
                        item = exports[name]
                        self._append_backend_output(f"  {name}\n    WAV: {item['wav']}\n    TXT: {item['txt']}\n    utterances: {item['utterances']}")
                self._save_authoring_session_for_path(path)
            except Exception as exc:
                self._generation_failed(
                    f"The plan loaded, but its authoring session could not be saved: {exc}"
                )
                return
            unresolved = []
            if self.score_model is not None:
                unresolved = [
                    issue for issue in self.score_model.validate(self.score_editor.toPlainText()).errors
                    if "needs resolution before animation" in issue.message
                ]
            if unresolved:
                self.generation_status.setText(
                    f"Performance Plan generated with {len(unresolved)} item(s) to resolve."
                )
                self.generation_status.setStyleSheet("color: #92400e;")
            else:
                self.generation_status.setText("Performance plan generated — animation setup incomplete.")
                self.generation_status.setStyleSheet("color: #166534;")
        else:
            self._generation_failed("The backend completed, but the generated plan could not be loaded.")

    def _generation_failed(self, message: str) -> None:
        self.generate_plan_button.setEnabled(True)
        self.generation_status.setText("Performance plan generation failed.")
        self.generation_status.setStyleSheet("color: #9b1c1c;")
        self._append_backend_output(message)
        lines = [line.strip() for line in str(message).splitlines() if line.strip()]
        concise = lines[-1] if lines else "Unknown backend error."
        QtWidgets.QMessageBox.critical(
            self,
            "Could Not Generate Performance Plan",
            concise,
        )

    def _look_at_mapping_data(self) -> list[dict[str, str]]:
        return [
            {
                "semantic_target": semantic.text().strip(),
                "maya_node": maya_object.text().strip(),
            }
            for semantic, maya_object, _row in self.look_at_rows
            if semantic.text().strip() or maya_object.text().strip()
        ]

    def generate_animation(self) -> None:
        if self.mode_combo.currentIndex() == 1:
            self._generate_dual_speaker_emotion()
            return
        if self.plan is None or self.score_model is None or self.source_path is None:
            QtWidgets.QMessageBox.warning(
                self, "Performance Plan Required", "Generate or load a Performance Plan first."
            )
            return
        script = self.input_script.toPlainText()
        if not script.strip():
            QtWidgets.QMessageBox.warning(
                self, "Input Script Required", "Input Script is required to compile animation."
            )
            return
        audio_folder = self.audio_folder.text().strip()
        if not audio_folder:
            QtWidgets.QMessageBox.warning(
                self, "Input Audio Folder Required", "Select an Input Audio Folder first."
            )
            return
        script_character = self.character_rows[0][0].text().strip()
        character_node = self.character_rows[0][1].text().strip()
        if not script_character or not character_node:
            QtWidgets.QMessageBox.warning(
                self,
                "Character Mapping Required",
                "The active script character and Maya rig/node mapping are required.",
            )
            return
        plan_character = str(self.plan.get("target_character") or "").strip()
        if plan_character and script_character.upper() != plan_character.upper():
            QtWidgets.QMessageBox.warning(
                self,
                "Character Mapping Mismatch",
                f"The plan targets {plan_character!r}, but Character Mapping uses "
                f"{script_character!r}.",
            )
            return
        if not cmds.objExists(character_node):
            QtWidgets.QMessageBox.warning(
                self,
                "Character Node Missing",
                f"The active Maya character node does not exist:\n{character_node}",
            )
            return
        if not self.commit_current_event(show_error=True):
            return
        if not self.validate_score(show_dialog=True):
            return
        setup_issues = animation_setup_issues(
            plan=self.plan,
            audio_folder=audio_folder,
            characters=[{
                "script_name": script_character, "maya_node": character_node,
            }],
            look_at_mappings=self._look_at_mapping_data(),
            node_exists=lambda node: bool(cmds.objExists(node)),
        )
        if setup_issues:
            message = "Animation Setup is incomplete:\n\n" + "\n".join(
                f"- {issue}" for issue in setup_issues
            ) + "\n\nFill the missing mappings and click Generate Animation again."
            QtWidgets.QMessageBox.warning(self, "Animation Setup Incomplete", message)
            self._append_backend_output(message)
            return

        animation_dir = self.source_path.parent / "animation"
        runtime_plan = animation_dir / "performance_plan_runtime.json"
        try:
            if self.plan is not None:
                self.plan["acting_interpretation"] = self.acting_interpretation.toPlainText()
            self.plan = save_animation_runtime_plan(
                self.score_model,
                self.score_editor.toPlainText(),
                runtime_plan,
            )
            self._refresh_phrase_reason()
            self._refresh_metadata_and_diagnostics()
            self._save_authoring_session_for_path(self.source_path)
            fps = current_scene_fps()
        except Exception as exc:
            self._animation_failed(str(exc))
            return

        self.backend_log.clear()
        self.generate_animation_button.setEnabled(False)
        self.animation_status.setText("Generating animation...")
        self.animation_status.setStyleSheet("color: #1d4ed8;")
        try:
            command = self.animation_runner.start(
                performance_plan=runtime_plan,
                script=script,
                audio_folder=audio_folder,
                output_dir=animation_dir,
                fps=fps,
            )
        except Exception as exc:
            self._animation_failed(str(exc))
            return
        self._append_backend_output(f"Runtime Performance Plan: {runtime_plan}")
        self._append_backend_output(f"Animation output: {command.output_dir}")
        self._append_backend_output(f"Maya scene FPS: {fps}")

    def _generate_dual_speaker_emotion(self) -> None:
        if self.plan is None or self.score_model is None or self.source_path is None or not self.commit_current_event(show_error=True) or not self.validate_score(show_dialog=True): return
        script=self.input_script.toPlainText(); audio=self.audio_folder.text().strip()
        if not script.strip() or not audio:
            QtWidgets.QMessageBox.warning(self,"Animation Setup Incomplete","Input Script and Input Audio Folder are required."); return
        mappings: dict[str, dict[str, str]]={}; runtime: dict[str, dict[str, str]]={}; self.backend_log.clear()
        try:
            for alias, index in (("A",0),("B",1)):
                name=self.character_rows[index][0].text().strip(); node=self.character_rows[index][1].text().strip()
                if not name or not node or not cmds.objExists(node): raise RuntimeError(f"{alias}: valid script character and Maya rig mapping are required.")
                jsync=resolve_jsync_for_character(node)
                sound=str(cmds.getAttr(f"{jsync}.sound_file") or "").strip()
                if not sound: raise RuntimeError(f"{alias}: resolved jSync has no sound_file.")
                text_input_path=str(cmds.getAttr(f"{jsync}.text_input_path") or "").strip()
                transcript=resolve_jali_source_transcript_path(text_input_path, sound)
                mappings[alias]={"script_name":name,"maya_node":node}; runtime[alias]={"script_name":name,"sound_file":sound,"transcript_path":str(transcript)}
                self._append_backend_output(f"{alias} / {name}: jSync={jsync}; sound_file={sound}; text_input_path={text_input_path}; transcript_path={transcript}")
            if any(str(self.plan.get("characters",{}).get(a,"")).upper()!=runtime[a]["script_name"].upper() for a in ("A","B")): raise RuntimeError("Character Mapping does not match the dual Performance Plan.")
            animation_dir=self.source_path.parent/"animation"; runtime_plan=animation_dir/"performance_plan_runtime.json"; self.plan=save_animation_runtime_plan(self.score_model,self.score_editor.toPlainText(),runtime_plan); fps=current_scene_fps()
            self._pending_animation_mode="dual_emotion_only"; self._pending_dual_mappings=mappings; self.generate_animation_button.setEnabled(False); self.animation_status.setText("Generating dual speaker emotion..."); self.animation_status.setStyleSheet("color: #1d4ed8;")
            command=self.animation_runner.start_dual(performance_plan=runtime_plan,script=script,audio_folder=audio,output_dir=animation_dir,fps=fps,runtime_mapping=runtime)
            self._append_backend_output(f"Dual emotion-only output: {command.output_dir}")
        except Exception as exc: self._animation_failed(str(exc))

    def _animation_compile_succeeded(self, manifest_path: object) -> None:
        stream = io.StringIO()
        try:
            with redirect_stdout(stream), redirect_stderr(stream):
                if self._pending_animation_mode == "dual_emotion_only":
                    # Validate both User lanes before speaker realignment changes either rig.
                    listener_context = prepare_dual_listener_mask_artifacts(manifest_path=Path(str(manifest_path)), character_mappings=self._pending_dual_mappings)
                    result=apply_dual_speaker_emotion_artifacts(manifest_path=Path(str(manifest_path)), character_mappings=self._pending_dual_mappings)
                    listener_result = apply_dual_listener_mask_artifacts(prepared_context=listener_context)
                    for alias, item in result.items(): self._append_backend_output(f"{alias} / {item['script_name']}: jSync={item['jsync_node']}; staging={item['staging_dir']}; mask_tags={item['mask_tag_count']}; heart_tags={item['heart_tag_count']}; realign={'completed' if item['realign_completed'] else 'failed'}; calculate_paralinguals={item['calculate_paralinguals']}; calculate_expression={item['calculate_expression']}; calculate_blinks=false; paths_restored={'yes' if item['paths_restored'] else 'no'}; mask_binding={'applied' if item['mask_binding'] else 'skipped'}; heart_binding={'applied' if item['heart_binding'] else 'skipped'}")
                    for alias in ("A", "B"):
                        item = listener_result[alias]
                        self._append_backend_output(f"{alias}: listener_mask_events={item['listener_mask_events']}; managed_user_plugs={len(item['managed_user_plugs'])}; eyelid_channels_filtered=yes; FACS_animationSource=Add")
                    self._append_backend_output("Applied: native speaker Mask/Heart; listener User Mask reactions\nNot applied: listener Heart; gaze; blink/lid; head\njSync preserved: yes")
                else: apply_animation_artifacts(
                    manifest_path=Path(str(manifest_path)),
                    active_character_node=self.character_rows[0][1].text().strip(),
                    look_at_mappings=self._look_at_mapping_data(),
                )
        except Exception as exc:
            self._append_backend_output(stream.getvalue())
            self._animation_failed(f"Maya apply failed: {exc}")
            return
        self._append_backend_output(stream.getvalue())
        self.generate_animation_button.setEnabled(True)
        self.animation_status.setText("Dual speaker emotion applied." if self._pending_animation_mode == "dual_emotion_only" else "Animation generated.")
        self.animation_status.setStyleSheet("color: #166534;")
        self._pending_animation_mode = "single"; self._pending_dual_mappings = {}
        QtWidgets.QMessageBox.information(
            self, "Animation Generated", "Animation artifacts were compiled and applied in Maya."
        )

    def _animation_failed(self, message: str) -> None:
        self._pending_animation_mode = "single"; self._pending_dual_mappings = {}
        self.generate_animation_button.setEnabled(True)
        self.animation_status.setText("Animation generation failed.")
        self.animation_status.setStyleSheet("color: #9b1c1c;")
        self._append_backend_output(message)
        lines = [line.strip() for line in str(message).splitlines() if line.strip()]
        QtWidgets.QMessageBox.critical(
            self,
            "Could Not Generate Animation",
            lines[-1] if lines else "Unknown animation error.",
        )

    def _known_look_targets(self) -> list[str]:
        return [field.text().strip() for field, _maya, _row in self.look_at_rows if field.text().strip()]

    def _clear_look_at_targets(self) -> None:
        for _semantic, _maya, row in self.look_at_rows:
            self.look_at_layout.removeWidget(row)
            row.deleteLater()
        self.look_at_rows = []

    def _refresh_required_look_at_targets(self) -> None:
        if self.plan is None:
            return
        rows = refresh_look_at_mappings(
            required_look_at_targets(self.plan), self._look_at_mapping_data()
        )
        self._clear_look_at_targets()
        for mapping in rows:
            self._add_look_at_target(mapping["semantic_target"])
            self.look_at_rows[-1][1].setText(mapping["maya_node"])

    def _restore_authoring_session(
        self, session: dict[str, Any], *, preserve_authoring_text: bool = False
    ) -> None:
        mode = str(session.get("mode") or "single")
        blocker = QtCore.QSignalBlocker(self.mode_combo)
        self.mode_combo.setCurrentIndex(1 if mode == "dual" else 0)
        del blocker
        self._update_character_mode()
        if not preserve_authoring_text:
            self.input_script.setPlainText(str(session.get("input_script") or ""))
            self.input_context.setPlainText(str(session.get("input_context") or ""))
        self.audio_folder.setText(str(session.get("audio_folder") or ""))
        for script_field, maya_field, _row in self.character_rows:
            script_field.clear()
            maya_field.clear()
        missing_nodes: list[str] = []
        for index, mapping in enumerate(session.get("characters", [])):
            if index >= len(self.character_rows) or not isinstance(mapping, dict):
                continue
            self.character_rows[index][0].setText(str(mapping.get("script_name") or ""))
            maya_node = str(mapping.get("maya_node") or "")
            self.character_rows[index][1].setText(maya_node)
            if maya_node and not cmds.objExists(maya_node):
                missing_nodes.append(maya_node)
        self._clear_look_at_targets()
        for mapping in session.get("look_at_targets", []):
            if not isinstance(mapping, dict):
                continue
            self._add_look_at_target(str(mapping.get("semantic_target") or ""))
            maya_node = str(mapping.get("maya_node") or "")
            self.look_at_rows[-1][1].setText(maya_node)
            if maya_node and not cmds.objExists(maya_node):
                missing_nodes.append(maya_node)
        if not self.look_at_rows:
            self._add_look_at_target("CRYSTAL")
        if missing_nodes:
            QtWidgets.QMessageBox.warning(
                self,
                "Missing Maya Session Nodes",
                "These saved Maya nodes are not present in the current scene. Their mappings were retained:\n\n"
                + "\n".join(missing_nodes),
            )

    def _build_authoring_session_data(self) -> dict[str, Any]:
        dual = self.mode_combo.currentIndex() == 1
        character_count = 2 if dual else 1
        characters = [
            {
                "alias": "A" if index == 0 else "B",
                "script_name": self.character_rows[index][0].text().strip(),
                "maya_node": self.character_rows[index][1].text().strip(),
            }
            for index in range(character_count)
        ]
        return build_authoring_session(
            sequence_id=str((self.plan or {}).get("sequence_id") or ""),
            mode="dual" if dual else "single",
            audio_folder=self.audio_folder.text(),
            input_script=self.input_script.toPlainText(),
            input_context=self.input_context.toPlainText(),
            characters=characters,
            look_at_targets=self._look_at_mapping_data(),
            base=self.authoring_session,
        )

    def _save_authoring_session_for_path(self, plan_path: Path) -> Path:
        session = self._build_authoring_session_data()
        session_path = default_authoring_session_path(
            plan_path, str((self.plan or {}).get("sequence_id") or "")
        )
        save_authoring_session(session, session_path)
        self.authoring_session = session
        return session_path

    def _score_changed(self) -> None:
        if not self._building and self.score_model is not None:
            self.validation_label.setText("Score changed — validate before saving.")
            self.validation_label.setStyleSheet("color: #92400e;")

    def validate_score(self, *, show_dialog: bool = False) -> bool:
        if self.score_model is None:
            if show_dialog:
                QtWidgets.QMessageBox.information(self, "No Plan", "Load a Performance Plan first.")
            return False
        self.score_model.targets.update(target.upper() for target in self._known_look_targets())
        result = self.score_model.validate(self.score_editor.toPlainText())
        if result.valid:
            self.validation_label.setText(f"Valid score — {len(result.phrases)} phrases")
            self.validation_label.setStyleSheet("color: #166534;")
            self.validation_details.clear()
            self.validation_details.hide()
            return True
        details = "\n".join(str(error) for error in result.errors)
        self.validation_label.setText(f"Invalid score — {len(result.errors)} error(s)")
        self.validation_label.setStyleSheet("color: #9b1c1c;")
        self.validation_details.setPlainText(details)
        self.validation_details.show()
        if show_dialog:
            QtWidgets.QMessageBox.warning(self, "Invalid Semantic Performance Score", details)
        return False

    def apply_score_edits(self, *, show_success: bool = True) -> bool:
        if not self.validate_score(show_dialog=True) or self.score_model is None:
            return False
        self.plan = self.score_model.apply(self.score_editor.toPlainText())
        self._refresh_required_look_at_targets()
        self._refresh_phrase_reason()
        self._refresh_metadata_and_diagnostics()
        if show_success:
            QtWidgets.QMessageBox.information(
                self, "Score Applied", "Valid semantic edits were applied to the canonical Performance Plan."
            )
        return True

    def _build_event_metadata(self) -> None:
        group = QtWidgets.QGroupBox("Event Metadata")
        form = QtWidgets.QFormLayout(group)
        self.event_id = QtWidgets.QLineEdit()
        self.event_id.setReadOnly(True)
        self.intent = QtWidgets.QLineEdit()
        self.transcript = QtWidgets.QPlainTextEdit()
        _configure_multiline_editor(
            self.transcript, height=72, read_only=True, fixed_height=True
        )
        self.char_start = QtWidgets.QLineEdit()
        self.char_start.setReadOnly(True)
        self.char_end = QtWidgets.QLineEdit()
        self.char_end.setReadOnly(True)
        form.addRow("Event ID", self.event_id)
        form.addRow("Intent", self.intent)
        form.addRow("Transcript", self.transcript)
        form.addRow("char_start", self.char_start)
        form.addRow("char_end", self.char_end)
        self.right_layout.addWidget(group)

    def _create_table(self, title: str, headers: list[str]) -> QtWidgets.QTableWidget:
        group = QtWidgets.QGroupBox(title)
        group_layout = QtWidgets.QVBoxLayout(group)
        table = QtWidgets.QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        table.setMinimumHeight(116)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        group_layout.addWidget(table)
        self.right_layout.addWidget(group)
        return table

    def _build_rationale(self) -> None:
        group = QtWidgets.QGroupBox("Rationale")
        group_layout = QtWidgets.QVBoxLayout(group)
        self.rationale = QtWidgets.QPlainTextEdit()
        _configure_multiline_editor(self.rationale, height=180, read_only=True)
        group_layout.addWidget(self.rationale)
        self.right_layout.addWidget(group)

    def _build_locks(self) -> None:
        group = QtWidgets.QGroupBox("Locks")
        layout = QtWidgets.QHBoxLayout(group)
        self.lock_checks: dict[str, QtWidgets.QCheckBox] = {}
        labels = {
            "intent": "Lock Intent",
            "affect": "Lock Affect",
            "gaze": "Lock Gaze",
            "head": "Lock Head",
            "blink": "Lock Blink",
        }
        for key, label in labels.items():
            checkbox = QtWidgets.QCheckBox(label)
            self.lock_checks[key] = checkbox
            layout.addWidget(checkbox)
        layout.addStretch(1)
        self.right_layout.addWidget(group)

    def load_plan_dialog(self) -> None:
        path, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load Existing Performance Plan",
            str(self.source_path.parent if self.source_path else Path.cwd()),
            "Performance Plan JSON (*.json)",
        )
        if path:
            self.load_plan(Path(path))

    def load_plan(self, path: Path, *, preserve_authoring_text: bool = False) -> None:
        try:
            loaded_plan = load_performance_plan(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could Not Load Plan", str(exc))
            return

        self.authoring_session = None
        try:
            sequence_id = str(loaded_plan.get("sequence_id") or "")
            session_path = default_authoring_session_path(path, sequence_id)
            self.authoring_session = (
                load_authoring_session(session_path) if session_path.exists() else None
            )
            if self.authoring_session is not None:
                self._restore_authoring_session(
                    self.authoring_session,
                    preserve_authoring_text=preserve_authoring_text,
                )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "Could Not Restore Authoring Session",
                f"The Performance Plan will still load, but its authoring-session sidecar could not be restored.\n\n{exc}",
            )
            self.authoring_session = None

        try:
            if loaded_plan.get("schema_version") == "dual_performance_plan_v0":
                self.score_model = DualPerformanceScoreModel(
                    loaded_plan, extra_targets=self._known_look_targets()
                )
                if self.authoring_session is None:
                    self.mode_combo.setCurrentIndex(1)
                    self._update_character_mode()
            else:
                self.score_model = PerformanceScoreModel(
                    loaded_plan, extra_targets=self._known_look_targets()
                )
            self.plan = self.score_model.plan
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could Not Load Plan", str(exc))
            return

        self.source_path = path
        self.current_event_index = None
        self._building = True
        events = [event for event in self.plan.get("events", []) if isinstance(event, dict)]
        dual_phrases = [
            phrase for phrase in self.plan.get("phrases", []) if isinstance(phrase, dict)
        ]
        if (
            not preserve_authoring_text
            and (not self.authoring_session or not self.authoring_session.get("input_script"))
        ):
            source_rows = events or dual_phrases
            self.input_script.setPlainText(" ".join(
                str(row.get("span", {}).get("text") or "") for row in source_rows
            ).strip())
        self.acting_interpretation.setPlainText(
            str(self.plan.get("acting_interpretation") or "")
        )
        self.score_editor.setPlainText(self.score_model.score_text)
        target_character = str(self.plan.get("target_character") or "")
        if target_character and self.authoring_session is None:
            self.character_rows[0][0].setText(target_character)
        if self.plan.get("schema_version") == "dual_performance_plan_v0" and self.authoring_session is None:
            characters = self.plan.get("characters", {})
            if isinstance(characters, dict):
                self.character_rows[0][0].setText(str(characters.get("A") or ""))
                self.character_rows[1][0].setText(str(characters.get("B") or ""))
        self.phrase_number.setMaximum(max(1, len(self.score_model.phrases)))
        self.phrase_number.setValue(1)
        self._building = False
        self.validate_score()
        self._refresh_required_look_at_targets()
        self._refresh_phrase_reason()
        self._refresh_metadata_and_diagnostics()
        self._building = True
        self.event_list.clear()
        for event in self.plan.get("events", []):
            event_id = event.get("event_id", "?")
            intent = event.get("intent") or "(no intent)"
            self.event_list.addItem(f"{event_id} — {intent}")
        self._building = False
        if self.event_list.count():
            self.event_list.setCurrentRow(0)
        else:
            self._clear_event_panel()

    def _refresh_phrase_reason(self) -> None:
        if self.score_model is None:
            self.phrase_reason.setPlainText("Load a Performance Plan to inspect phrase reasons.")
            return
        self.phrase_reason.setPlainText(
            format_rationale_view(self.score_model, self.phrase_number.value())
        )

    def _refresh_metadata_and_diagnostics(self) -> None:
        if self.plan is None:
            self.metadata_label.setText("No plan loaded")
            self.diagnostics.clear()
            return
        characters = self.plan.get("characters")
        character_label = (
            f"characters: {characters}" if isinstance(characters, dict)
            else f"target_character: {self.plan.get('target_character', '')}"
        )
        self.metadata_label.setText(
            "schema_version: {schema}    sequence_id: {sequence}    {character}".format(
                schema=self.plan.get("schema_version", ""),
                sequence=self.plan.get("sequence_id", ""),
                character=character_label,
            )
        )
        diagnostics = self.plan.get("diagnostics", {})
        errors = diagnostics.get("errors", []) if isinstance(diagnostics, dict) else []
        warnings = diagnostics.get("warnings", []) if isinstance(diagnostics, dict) else []
        lines = ["Errors:"] + ([f"- {item}" for item in errors] or ["- none"])
        lines += ["", "Warnings:"] + ([f"- {item}" for item in warnings] or ["- none"])
        self.diagnostics.setPlainText("\n".join(lines))
        self.diagnostics.setStyleSheet(
            "QPlainTextEdit { color: #9b1c1c; background: #fff5f5; }"
            if errors
            else "QPlainTextEdit { color: #1f2937; }"
        )

    def _select_event(self, row: int) -> None:
        if self._building or self.plan is None:
            return
        if self.current_event_index is not None and row != self.current_event_index:
            if not self.commit_current_event(show_error=True):
                blocker = QtCore.QSignalBlocker(self.event_list)
                self.event_list.setCurrentRow(self.current_event_index)
                del blocker
                return
        if row < 0 or row >= len(self.plan.get("events", [])):
            self.current_event_index = None
            self._clear_event_panel()
            return
        self.current_event_index = row
        self._populate_event(self.plan["events"][row])

    def _populate_event(self, event: dict[str, Any]) -> None:
        self._building = True
        span = event.get("span", {})
        self.event_id.setText(str(event.get("event_id", "")))
        self.intent.setText(str(event.get("intent") or ""))
        self.transcript.setPlainText(str(span.get("text", "")))
        self.char_start.setText(str(span.get("char_start", "")))
        self.char_end.setText(str(span.get("char_end", "")))

        affect = event.get("affect", {})
        blink = event.get("blink", {})
        self._populate_span_table(self.visible_affect, affect.get("visible", []), ["state", "intensity"])
        self._populate_span_table(self.hidden_affect, affect.get("hidden", []), ["state", "intensity"])
        self._populate_span_table(self.gaze, event.get("gaze", []), ["mode", "target"])
        self._populate_span_table(self.head, event.get("head", []), ["involvement"])
        self._populate_span_table(self.lid_state, event.get("lid_state", []), ["lid_state"])
        self._populate_span_table(self.performative_blink, blink.get("performative", []), ["value"])
        self._populate_span_table(self.blink_suppression, blink.get("suppression", []), ["value"])
        self.rationale.setPlainText(self._format_rationale(event.get("rationale", {})))

        locks = event.get("locks", {})
        for key, checkbox in self.lock_checks.items():
            checkbox.setChecked(bool(locks.get(key, False)))
        self._building = False

    def _populate_span_table(
        self,
        table: QtWidgets.QTableWidget,
        spans: Any,
        editable_keys: list[str],
    ) -> None:
        items = spans if isinstance(spans, list) else []
        table.setRowCount(len(items))
        for row, span in enumerate(items):
            data = span if isinstance(span, dict) else {}
            for column, key in enumerate(editable_keys):
                table.setItem(row, column, _editable_item(data.get(key)))
            offset = len(editable_keys)
            table.setItem(row, offset, _readonly_item(data.get("char_start")))
            table.setItem(row, offset + 1, _readonly_item(data.get("char_end")))
            table.setItem(row, offset + 2, _readonly_item(data.get("source_tag")))

    def _format_rationale(self, rationale: Any) -> str:
        data = rationale if isinstance(rationale, dict) else {}
        lines: list[str] = []

        def entries(title: str, value: Any) -> None:
            lines.append(title)
            rows = value if isinstance(value, list) else [value]
            actual_rows = [row for row in rows if isinstance(row, dict)]
            if not actual_rows:
                lines.append("  - none")
                return
            for row in actual_rows:
                lines.append(f"  - {row.get('source_tag', '?')}: {row.get('reason') or ''}")

        entries("Intent", data.get("intent"))
        affect = data.get("affect", {}) if isinstance(data.get("affect"), dict) else {}
        entries("Visible Affect", affect.get("visible"))
        entries("Hidden Affect", affect.get("hidden"))
        entries("Gaze", data.get("gaze"))
        entries("Head", data.get("head"))
        entries("Lid", data.get("lid_state"))
        blink = data.get("blink", {}) if isinstance(data.get("blink"), dict) else {}
        entries("Performative Blink", blink.get("performative"))
        entries("Blink Suppression", blink.get("suppression"))
        return "\n".join(lines)

    def _clear_event_panel(self) -> None:
        self._building = True
        self.event_id.clear()
        self.intent.clear()
        self.transcript.clear()
        self.char_start.clear()
        self.char_end.clear()
        for table in (
            self.visible_affect,
            self.hidden_affect,
            self.gaze,
            self.head,
            self.lid_state,
            self.performative_blink,
            self.blink_suppression,
        ):
            table.setRowCount(0)
        self.rationale.clear()
        for checkbox in self.lock_checks.values():
            checkbox.setChecked(False)
        self._building = False

    @staticmethod
    def _number(table: QtWidgets.QTableWidget, row: int, column: int, label: str) -> float | None:
        item = table.item(row, column)
        text = item.text().strip() if item is not None else ""
        if not text:
            return None
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{label} row {row + 1} must be numeric.") from exc

    @staticmethod
    def _text(table: QtWidgets.QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text() if item is not None else ""

    def _commit_affect_table(self, table: QtWidgets.QTableWidget, spans: Any, label: str) -> None:
        for row, span in enumerate(spans if isinstance(spans, list) else []):
            update_affect_span(
                span,
                self._text(table, row, 0),
                self._number(table, row, 1, f"{label} intensity"),
            )

    def _commit_gaze_table(self, event: dict[str, Any]) -> None:
        for row, span in enumerate(event.get("gaze", [])):
            update_gaze_span(span, self._text(self.gaze, row, 0), self._text(self.gaze, row, 1))

    def _commit_single_value_table(
        self,
        table: QtWidgets.QTableWidget,
        spans: Any,
        updater: Any,
        label: str,
    ) -> None:
        for row, span in enumerate(spans if isinstance(spans, list) else []):
            updater(span, self._number(table, row, 0, label))

    def commit_current_event(self, *, show_error: bool) -> bool:
        if self.plan is None or self.current_event_index is None:
            return True
        event = self.plan["events"][self.current_event_index]
        try:
            set_event_intent(event, self.intent.text())
            affect = event.get("affect", {})
            self._commit_affect_table(self.visible_affect, affect.get("visible", []), "Visible Affect")
            self._commit_affect_table(self.hidden_affect, affect.get("hidden", []), "Hidden Affect")
            self._commit_gaze_table(event)
            self._commit_single_value_table(self.head, event.get("head", []), update_head_span, "Head involvement")
            self._commit_single_value_table(self.lid_state, event.get("lid_state", []), update_lid_state_span, "Lid State")
            blink = event.get("blink", {})
            for row, span in enumerate(blink.get("performative", [])):
                update_blink_span(span, self._text(self.performative_blink, row, 0))
            for row, span in enumerate(blink.get("suppression", [])):
                update_blink_span(span, self._text(self.blink_suppression, row, 0))
            set_event_locks(event, {key: box.isChecked() for key, box in self.lock_checks.items()})
            list_item = self.event_list.item(self.current_event_index)
            if list_item is not None:
                list_item.setText(f"{event.get('event_id', '?')} — {event.get('intent') or '(no intent)'}")
            return True
        except (TypeError, ValueError) as exc:
            if show_error:
                QtWidgets.QMessageBox.warning(self, "Invalid Editable Value", str(exc))
            return False

    def save_edited_plan(self) -> None:
        if self.plan is None:
            QtWidgets.QMessageBox.information(self, "No Plan", "Load a Performance Plan first.")
            return
        if not self.commit_current_event(show_error=True):
            return
        if not self.apply_score_edits(show_success=False):
            return
        if self.source_path is None:
            self.save_edited_plan_as()
            return
        self._save_to(default_edited_path(self.source_path))

    def save_edited_plan_as(self) -> None:
        if self.plan is None:
            QtWidgets.QMessageBox.information(self, "No Plan", "Load a Performance Plan first.")
            return
        if not self.commit_current_event(show_error=True):
            return
        if not self.apply_score_edits(show_success=False):
            return
        suggested = default_edited_path(self.source_path) if self.source_path else Path.cwd() / "performance_plan_edited.json"
        path, _selected = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Performance Plan As",
            str(suggested),
            "Performance Plan JSON (*.json)",
        )
        if path:
            self._save_to(Path(path))

    def _save_to(self, path: Path) -> None:
        try:
            if self.plan is not None:
                self.plan["acting_interpretation"] = self.acting_interpretation.toPlainText()
            save_performance_plan(self.plan or {}, path)
            session_path = self._save_authoring_session_for_path(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could Not Save Authoring Data", str(exc))
            return
        QtWidgets.QMessageBox.information(
            self,
            "Plan Saved",
            f"Saved edited plan:\n{path}\n\nSaved authoring session:\n{session_path}",
        )


def show_performance_plan_editor() -> PerformancePlanEditor:
    """Close any older editor instance and show a fresh window in Maya."""
    global PERFORMANCE_PLAN_EDITOR
    if PERFORMANCE_PLAN_EDITOR is not None:
        PERFORMANCE_PLAN_EDITOR.close()
        PERFORMANCE_PLAN_EDITOR.deleteLater()
        PERFORMANCE_PLAN_EDITOR = None
    for widget in QtWidgets.QApplication.topLevelWidgets():
        if widget.objectName() == WINDOW_OBJECT_NAME:
            widget.close()
            widget.deleteLater()
    PERFORMANCE_PLAN_EDITOR = PerformancePlanEditor(parent=maya_main_window())
    PERFORMANCE_PLAN_EDITOR.show()
    PERFORMANCE_PLAN_EDITOR.raise_()
    PERFORMANCE_PLAN_EDITOR.activateWindow()
    return PERFORMANCE_PLAN_EDITOR
