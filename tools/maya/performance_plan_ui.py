"""Maya 2025 PySide6 editor for semantic Performance Plan JSON files.

This tool intentionally has no dependency on the Python 3.12 backend package.
It reads and writes only Performance Plan JSON through the adjacent data helper.
"""

from __future__ import annotations

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
    PerformanceScoreModel,
    format_rationale_view,
)
from authoring_session_data import (  # noqa: E402
    build_authoring_session,
    default_authoring_session_path,
    load_authoring_session,
    save_authoring_session,
)


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


class PerformancePlanEditor(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent or maya_main_window())
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Performance Plan Editor")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(1280, 860)

        self.plan: dict[str, Any] | None = None
        self.score_model: PerformanceScoreModel | None = None
        self.authoring_session: dict[str, Any] | None = None
        self.source_path: Path | None = None
        self.current_event_index: int | None = None
        self._building = False
        self.character_rows: list[tuple[QtWidgets.QLineEdit, QtWidgets.QLineEdit, QtWidgets.QWidget]] = []
        self.look_at_rows: list[tuple[QtWidgets.QLineEdit, QtWidgets.QLineEdit, QtWidgets.QWidget]] = []

        self._build_ui()

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

        bottom = QtWidgets.QHBoxLayout()
        self.generate_animation_button = QtWidgets.QPushButton("Generate Animation")
        self.generate_animation_button.setEnabled(False)
        self.generate_animation_button.setToolTip("Deferred in Phase 1; animation execution is not implemented.")
        bottom.addWidget(self.generate_animation_button)
        bottom.addStretch(1)
        authoring.addLayout(bottom)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, "Authoring")

    def _build_setup(self, parent: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("SETUP")
        layout = QtWidgets.QVBoxLayout(group)
        layout.addWidget(QtWidgets.QLabel("Input Script"))
        self.input_script = QtWidgets.QPlainTextEdit()
        self.input_script.setPlaceholderText("Paste or load the dialogue/script used for the performance plan.")
        self.input_script.setMinimumHeight(100)
        layout.addWidget(self.input_script)

        audio = QtWidgets.QHBoxLayout()
        audio.addWidget(QtWidgets.QLabel("Input Audio Folder"))
        self.audio_folder = QtWidgets.QLineEdit()
        audio.addWidget(self.audio_folder, 1)
        choose_audio = QtWidgets.QPushButton("Select Folder")
        choose_audio.clicked.connect(self._select_audio_folder)
        audio.addWidget(choose_audio)
        layout.addLayout(audio)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Authoring Mode"))
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Single Character", "Dual Character"])
        self.mode_combo.currentIndexChanged.connect(self._update_character_mode)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        layout.addWidget(QtWidgets.QLabel("Character Mapping"))
        character_grid = QtWidgets.QGridLayout()
        character_grid.addWidget(QtWidgets.QLabel("Script Character"), 0, 0)
        character_grid.addWidget(QtWidgets.QLabel("Maya Rig / Node"), 0, 2)
        for row_index in range(2):
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            script_name = QtWidgets.QLineEdit()
            script_name.setPlaceholderText("PROFESSOR" if row_index == 0 else "DOROTHY")
            rig_name = QtWidgets.QLineEdit()
            rig_name.setPlaceholderText("Select a rig or Maya node")
            select = QtWidgets.QPushButton("Use Scene Selection")
            select.clicked.connect(lambda _checked=False, field=rig_name: self._use_scene_selection(field))
            row_layout.addWidget(script_name, 1)
            row_layout.addWidget(QtWidgets.QLabel("→"))
            row_layout.addWidget(rig_name, 1)
            row_layout.addWidget(select)
            character_grid.addWidget(row_widget, row_index + 1, 0, 1, 4)
            self.character_rows.append((script_name, rig_name, row_widget))
        layout.addLayout(character_grid)

        layout.addWidget(QtWidgets.QLabel("Potential Look-at Target Mapping"))
        self.look_at_layout = QtWidgets.QVBoxLayout()
        layout.addLayout(self.look_at_layout)
        add_target = QtWidgets.QPushButton("+ Add Look-at Target")
        add_target.clicked.connect(self._add_look_at_target)
        layout.addWidget(add_target, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        self._add_look_at_target("CRYSTAL")

        self.generate_plan_button = QtWidgets.QPushButton("Generate Performance Plan")
        self.generate_plan_button.clicked.connect(
            lambda: self._show_phase_one_placeholder("Generate Performance Plan")
        )
        layout.addWidget(self.generate_plan_button)
        parent.addWidget(group)
        self._update_character_mode()

    def _build_acting_interpretation(self, parent: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("ACTING INTERPRETATION")
        layout = QtWidgets.QVBoxLayout(group)
        self.acting_interpretation = QtWidgets.QPlainTextEdit()
        self.acting_interpretation.setPlaceholderText(
            "Scene:\n\nAffective state:\n\nNarrative intent:"
        )
        self.acting_interpretation.setMinimumHeight(130)
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
        self.score_editor.setMinimumHeight(260)
        self.score_editor.textChanged.connect(self._score_changed)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        self.score_editor.setFont(font)
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
        self.validation_details.setReadOnly(True)
        self.validation_details.setMaximumHeight(90)
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
        self.phrase_reason.setReadOnly(True)
        self.phrase_reason.setMinimumHeight(160)
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

        self.diagnostics = QtWidgets.QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumBlockCount(200)
        self.diagnostics.setFixedHeight(82)
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
        field.setText(str(selected[0]))

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
        if dual:
            self.validation_label.setText(
                "Dual UI mode selected; load paired character plans through future backend plumbing."
            )

    def _show_phase_one_placeholder(self, action: str) -> None:
        QtWidgets.QMessageBox.information(
            self,
            f"{action} — Phase 1",
            f"{action} backend execution is deferred. The UI plumbing is present, but Maya does not call the backend or an LLM.",
        )

    def _known_look_targets(self) -> list[str]:
        return [field.text().strip() for field, _maya, _row in self.look_at_rows if field.text().strip()]

    def _clear_look_at_targets(self) -> None:
        for _semantic, _maya, row in self.look_at_rows:
            self.look_at_layout.removeWidget(row)
            row.deleteLater()
        self.look_at_rows = []

    def _restore_authoring_session(self, session: dict[str, Any]) -> None:
        mode = str(session.get("mode") or "single")
        blocker = QtCore.QSignalBlocker(self.mode_combo)
        self.mode_combo.setCurrentIndex(1 if mode == "dual" else 0)
        del blocker
        self._update_character_mode()
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
        look_at_targets = [
            {
                "semantic_target": semantic.text().strip(),
                "maya_node": maya_object.text().strip(),
            }
            for semantic, maya_object, _row in self.look_at_rows
            if semantic.text().strip() or maya_object.text().strip()
        ]
        return build_authoring_session(
            sequence_id=str((self.plan or {}).get("sequence_id") or ""),
            mode="dual" if dual else "single",
            audio_folder=self.audio_folder.text(),
            characters=characters,
            look_at_targets=look_at_targets,
            base=self.authoring_session,
        )

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
        self.transcript.setReadOnly(True)
        self.transcript.setFixedHeight(72)
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
        self.rationale.setReadOnly(True)
        self.rationale.setMinimumHeight(180)
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

    def load_plan(self, path: Path) -> None:
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
                self._restore_authoring_session(self.authoring_session)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "Could Not Restore Authoring Session",
                f"The Performance Plan will still load, but its authoring-session sidecar could not be restored.\n\n{exc}",
            )
            self.authoring_session = None

        try:
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
        self.input_script.setPlainText(" ".join(
            str(event.get("span", {}).get("text") or "") for event in events
        ).strip())
        self.acting_interpretation.setPlainText(
            str(self.plan.get("acting_interpretation") or "")
        )
        self.score_editor.setPlainText(self.score_model.score_text)
        target_character = str(self.plan.get("target_character") or "")
        if target_character and self.authoring_session is None:
            self.character_rows[0][0].setText(target_character)
        self.phrase_number.setMaximum(max(1, len(self.score_model.phrases)))
        self.phrase_number.setValue(1)
        self._building = False
        self.validate_score()
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
        self.metadata_label.setText(
            "schema_version: {schema}    sequence_id: {sequence}    target_character: {character}".format(
                schema=self.plan.get("schema_version", ""),
                sequence=self.plan.get("sequence_id", ""),
                character=self.plan.get("target_character", ""),
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
            session = self._build_authoring_session_data()
            save_performance_plan(self.plan or {}, path)
            session_path = default_authoring_session_path(
                path, str((self.plan or {}).get("sequence_id") or "")
            )
            save_authoring_session(session, session_path)
            self.authoring_session = session
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
