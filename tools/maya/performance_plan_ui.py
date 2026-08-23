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
        self.source_path: Path | None = None
        self.current_event_index: int | None = None
        self._building = False

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QHBoxLayout()
        self.load_button = QtWidgets.QPushButton("Load Plan")
        self.load_button.clicked.connect(self.load_plan_dialog)
        top.addWidget(self.load_button)
        self.metadata_label = QtWidgets.QLabel("No plan loaded")
        self.metadata_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.metadata_label, 1)
        layout.addLayout(top)

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

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        self.save_button = QtWidgets.QPushButton("Save Edited Plan")
        self.save_button.clicked.connect(self.save_edited_plan)
        bottom.addWidget(self.save_button)
        self.save_as_button = QtWidgets.QPushButton("Save Edited Plan As...")
        self.save_as_button.clicked.connect(self.save_edited_plan_as)
        bottom.addWidget(self.save_as_button)
        layout.addLayout(bottom)

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
            "Load Performance Plan",
            str(self.source_path.parent if self.source_path else Path.cwd()),
            "Performance Plan JSON (*.json)",
        )
        if path:
            self.load_plan(Path(path))

    def load_plan(self, path: Path) -> None:
        try:
            self.plan = load_performance_plan(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could Not Load Plan", str(exc))
            return

        self.source_path = path
        self.current_event_index = None
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
        suggested = default_edited_path(self.source_path) if self.source_path else Path.cwd() / "performance_plan_edited.json"
        path, _selected = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Edited Performance Plan",
            str(suggested),
            "Performance Plan JSON (*.json)",
        )
        if path:
            self._save_to(Path(path))

    def _save_to(self, path: Path) -> None:
        try:
            save_performance_plan(self.plan or {}, path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could Not Save Plan", str(exc))
            return
        QtWidgets.QMessageBox.information(self, "Plan Saved", f"Saved edited plan:\n{path}")


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
