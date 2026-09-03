"""Maya 2025 PySide6 editor for semantic Performance Plan JSON files.

This tool intentionally has no dependency on the Python 3.12 backend package.
It reads and writes only Performance Plan JSON through the adjacent data helper.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import math
import os
from pathlib import Path
import sys
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import wrapInstance
from maya import OpenMayaUI as omui
from maya import cmds
from maya import mel


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from performance_plan_ui_data import (  # noqa: E402
    default_edited_path,
    canonical_dual_authored_content,
    is_dual_plan_changed_from_loaded_snapshot,
    is_dual_plan_edited,
    load_performance_plan,
    save_animation_runtime_plan,
    save_performance_plan,
    score_text_matches_clean_baseline,
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
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
from dual_sparse_score_model import DualSparseScoreModel, projection_offset_from_score_plain_offset  # noqa: E402
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model, speaker_key  # noqa: E402
from authoring_session_data import (  # noqa: E402
    STUDY_UI_NORMAL,
    build_inspection_event,
    build_semantic_edit_event,
    build_study_ui_session,
    build_authoring_session,
    default_authoring_session_path,
    finish_study_ui_session,
    load_authoring_session,
    normalize_study_ui_mode,
    record_study_ui_mode_change,
    rebind_character_mappings,
    runtime_character_mappings,
    save_authoring_session,
    study_ui_section_state,
)
from animation_apply_runner import (  # noqa: E402
    apply_animation_artifacts,
    apply_master_audio_to_maya_timeline,
    apply_dual_listener_mask_artifacts,
    capture_dual_jali_base_if_absent,
    apply_dual_gaze_only_artifacts,
    apply_dual_speaker_emotion_artifacts,
    current_scene_fps,
    qualify_rig_control,
    resolve_jali_source_transcript_path,
    resolve_jsync_for_character,
    prepare_dual_gaze_only_artifacts,
    prepare_dual_listener_mask_artifacts,
    prepare_legacy_dual_listener_mask_artifacts,
    prepare_dual_gaze_artifacts,
    prepare_dual_head_blink_overlays,
    apply_dual_head_blink_overlays,
    diagnose_blink_ownership,
    master_audio_timeline_info,
    restore_dual_jali_base,
)
from authoring_requirements import (  # noqa: E402
    animation_setup_issues,
    refresh_look_at_mappings,
    required_look_at_targets,
)
from dual_source_transcripts import export_dual_source_transcripts, resolve_character_wav, resolve_dual_master_wav  # noqa: E402
from jali_speech_base import (  # noqa: E402
    ensure_dual_jali_speech_bases,
    jali_speech_settings_for_audio_folder,
    normalize_jali_speech_settings,
    resolve_existing_jali_speech_base,
    speech_base_status_text,
)
from dual_gaze_calibration import capture_target_pose_and_restore, required_calibration_pairs, calibration_key, display_target, optional_look_at_validation_error  # noqa: E402
from backend_process_runner import AnimationProcessRunner, BackendProcessRunner  # noqa: E402


WINDOW_OBJECT_NAME = "jalitestPerformancePlanEditor"
PERFORMANCE_PLAN_EDITOR: "PerformancePlanEditor | None" = None
_SCORE_EDITOR_MIN_HEIGHT = 260
_SCORE_EDITOR_MAX_HEIGHT = 650


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


def _resize_semantic_score_editor(editor: QtWidgets.QPlainTextEdit) -> None:
    """Fit a score editor to its wrapped document, within the Authoring page."""
    document = editor.document()
    document.setTextWidth(max(1, editor.viewport().width()))
    document_height = document.documentLayout().documentSize().height()
    contents = editor.contentsMargins()
    chrome_height = (
        (2 * editor.frameWidth())
        + (2 * math.ceil(document.documentMargin()))
        + contents.top()
        + contents.bottom()
        + 4
    )
    content_height = math.ceil(document_height) + chrome_height
    target_height = max(_SCORE_EDITOR_MIN_HEIGHT, min(_SCORE_EDITOR_MAX_HEIGHT, content_height))
    editor.setVerticalScrollBarPolicy(
        QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        if content_height > _SCORE_EDITOR_MAX_HEIGHT
        else QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    editor.setFixedHeight(target_height)


def panel_dialogue_role(panel_actor: str, speaker: str) -> str:
    return "speaking" if speaker_key(panel_actor) == speaker_key(speaker) else "listening"


class CollapsibleSection(QtWidgets.QWidget):
    """A keyboard-accessible header whose body is removed from layout when closed."""

    toggled = QtCore.Signal(bool)

    def __init__(
        self,
        title: str,
        *,
        expanded: bool = True,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.header_button = QtWidgets.QPushButton()
        self.header_button.setCheckable(True)
        self.header_button.setAccessibleName(title)
        self.header_button.clicked.connect(self._user_toggled)
        layout.addWidget(self.header_button)
        self.body = QtWidgets.QWidget()
        self._body_layout = QtWidgets.QVBoxLayout(self.body)
        layout.addWidget(self.body)
        self.set_expanded(expanded)

    def body_layout(self) -> QtWidgets.QVBoxLayout:
        return self._body_layout

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        blocker = QtCore.QSignalBlocker(self.header_button)
        self.header_button.setChecked(expanded)
        del blocker
        self._apply_expanded(expanded)

    def is_expanded(self) -> bool:
        return self.header_button.isChecked()

    def _apply_expanded(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.header_button.setText(f"{'▼' if expanded else '▶'} {self._title}")
        self.header_button.setAccessibleDescription(
            "Expanded; activate to collapse" if expanded else "Collapsed; activate to expand"
        )

    def _user_toggled(self, expanded: bool) -> None:
        self._apply_expanded(expanded)
        self.toggled.emit(expanded)


class _SparseScoreHighlighter(QtGui.QSyntaxHighlighter):
    """Color immutable dialogue by original speaker and tags distinctly."""

    def __init__(self, document: QtGui.QTextDocument, projection: Any, characters: list[str], *, panel_actor: str):
        super().__init__(document)
        self.projection = projection
        self.characters = list(characters)
        self.panel_actor = panel_actor
        self.speaker_formats = {}
        for role, color in (("speaking", "#b58900"), ("listening", "#2563eb")):
            fmt = QtGui.QTextCharFormat()
            fmt.setForeground(QtGui.QColor(color))
            self.speaker_formats[role] = fmt
        self.tag_format = QtGui.QTextCharFormat()
        self.tag_format.setForeground(QtGui.QColor("#c026d3"))
        self.tag_format.setFontWeight(QtGui.QFont.Weight.Bold)

    def highlightBlock(self, text: str) -> None:
        block_start = self.currentBlock().position()
        whole = self.document().toPlainText()
        before = whole[:block_start]
        plain_offset = projection_offset_from_score_plain_offset(
            whole, len(__import__("re").sub(r"<[^<>\r\n]+>", "", before))
        )
        ranges = self.projection.speaker_ranges
        characters = []
        in_tag = False
        tag_start = 0
        for index, char in enumerate(text):
            if char == "<" and not in_tag:
                in_tag, tag_start = True, index
            if in_tag:
                if char == ">":
                    self.setFormat(tag_start, index - tag_start + 1, self.tag_format)
                    in_tag = False
                continue
            speaker_index = next((i for i, row in enumerate(ranges) if row.start <= plain_offset < row.end), None)
            if speaker_index is not None:
                speaker = ranges[speaker_index].speaker
                self.setFormat(index, 1, self.speaker_formats[panel_dialogue_role(self.panel_actor, speaker)])
            plain_offset += 1

class PerformancePlanEditor(QtWidgets.QDialog):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        study_ui_mode: str | None = None,
    ) -> None:
        super().__init__(parent or maya_main_window())
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Performance Plan Editor")
        self.setWindowFlag(
            QtCore.Qt.WindowType.WindowMinimizeButtonHint,
            True,
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(1280, 860)

        self.plan: dict[str, Any] | None = None
        self.score_model: PerformanceScoreModel | DualPerformanceScoreModel | DualSparseScoreModel | None = None
        self.authoring_session: dict[str, Any] | None = None
        self.source_path: Path | None = None
        self._loaded_dual_snapshot_content: dict[str, Any] | None = None
        self._generation_had_active_plan = False
        self._suppress_score_dirty_tracking = False
        self._clean_score_baseline: str | dict[str, str] | None = None
        self.current_event_index: int | None = None
        self._building = False
        self.character_rows: list[tuple[QtWidgets.QLineEdit, QtWidgets.QLineEdit, QtWidgets.QWidget]] = []
        self.character_mapping_rows: list[QtWidgets.QWidget] = []
        self.look_at_rows: list[tuple[QtWidgets.QLineEdit, QtWidgets.QLineEdit, QtWidgets.QWidget]] = []
        self.dual_gaze_calibrations: dict[str, dict[str, list[float]]] = {}
        self.dual_gaze_baselines: dict[str, dict[str, object]] = {}
        self.jali_base_baseline: dict[str, Any] | None = None
        self.jali_speech_bases: dict[str, dict[str, Any]] = {}
        self._pending_animation_mode = "single"
        self._pending_dual_mappings: dict[str, dict[str, str]] = {}
        self._pending_dual_master_audio: dict[str, Any] | None = None
        self._score_resize_scheduled = False
        self.study_ui_mode = normalize_study_ui_mode(
            study_ui_mode or os.getenv("JALITEST_STUDY_UI_MODE") or STUDY_UI_NORMAL
        )
        self._inspection_events: list[dict[str, str]] = []
        self._semantic_edit_events: list[dict[str, str]] = []
        self._semantic_edit_active = False
        self._study_ui_session = build_study_ui_session(self.study_ui_mode)

        self._build_ui()
        self._apply_study_ui_mode()
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
        self.legacy_look_at_label = QtWidgets.QLabel("Required Look-at Targets")
        layout.addWidget(self.legacy_look_at_label)
        self.look_at_layout = QtWidgets.QVBoxLayout()
        layout.addLayout(self.look_at_layout)
        self.gaze_calibration_label = QtWidgets.QLabel("LOOK-AT CALIBRATION")
        layout.addWidget(self.gaze_calibration_label)
        self.gaze_calibration_layout = QtWidgets.QVBoxLayout()
        layout.addLayout(self.gaze_calibration_layout)
        layout.addWidget(QtWidgets.QLabel("JALI SPEECH BASE"))
        self.jali_speech_status_labels: dict[str, QtWidgets.QLabel] = {}
        for alias in ("A", "B"):
            label = QtWidgets.QLabel(speech_base_status_text(alias, "", "will_prepare"))
            layout.addWidget(label)
            self.jali_speech_status_labels[alias] = label
        self.audio_folder.textChanged.connect(self._refresh_jali_speech_status_preview)
        for script_field, maya_field, _row in self.character_rows:
            script_field.textChanged.connect(self._refresh_jali_speech_status_preview)
            maya_field.textChanged.connect(self._refresh_jali_speech_status_preview)
        optional = QtWidgets.QHBoxLayout()
        optional.addWidget(QtWidgets.QLabel("Optional Scene Target"))
        self.optional_gaze_actor = QtWidgets.QComboBox(); optional.addWidget(self.optional_gaze_actor)
        self.optional_gaze_target = QtWidgets.QComboBox(); self.optional_gaze_target.setEditable(True); optional.addWidget(self.optional_gaze_target, 1)
        capture_optional = QtWidgets.QPushButton("Capture Optional Look-at")
        capture_optional.clicked.connect(self._capture_optional_dual_look_at)
        optional.addWidget(capture_optional); layout.addLayout(optional)
        bottom = QtWidgets.QHBoxLayout()
        self.prepare_jali_speech_next = QtWidgets.QCheckBox(
            "Prepare JALI Speech on next Generate"
        )
        self.prepare_jali_speech_next.setChecked(False)
        self.prepare_jali_speech_next.setToolTip(
            "Unchecked: use the existing per-character jSync nodes and apply only "
            "JALITEST overlays. Checked: verify or prepare native JALI speech first."
        )
        self.prepare_jali_speech_next.toggled.connect(
            self._refresh_jali_speech_status_preview
        )
        bottom.addWidget(self.prepare_jali_speech_next)
        self.generate_animation_button = QtWidgets.QPushButton("Generate Animation")
        self.generate_animation_button.clicked.connect(self.generate_animation)
        bottom.addWidget(self.generate_animation_button)
        self.restore_jali_base_button = QtWidgets.QPushButton("Restore JALI Base")
        self.restore_jali_base_button.clicked.connect(self._restore_jali_base)
        bottom.addWidget(self.restore_jali_base_button)
        self.animation_status = QtWidgets.QLabel("Ready.")
        bottom.addWidget(self.animation_status)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        authoring.addWidget(group)
        self._refresh_jali_speech_status_preview()
        self._update_character_mode()

    def _build_setup(self, parent: QtWidgets.QVBoxLayout) -> None:
        group = QtWidgets.QGroupBox("SETUP")
        layout = QtWidgets.QVBoxLayout(group)
        layout.addWidget(QtWidgets.QLabel("Dialogue"))
        self.input_script = QtWidgets.QPlainTextEdit()
        self.input_script.setPlaceholderText("Paste or enter the dialogue used for the performance.")
        _configure_multiline_editor(self.input_script, height=240)
        layout.addWidget(self.input_script)

        layout.addWidget(QtWidgets.QLabel("Acting Direction (Optional)"))
        self.input_context = QtWidgets.QPlainTextEdit()
        self.input_context.setPlaceholderText(
            "Optional acting direction, scene information, character motivation, or performance constraints."
        )
        _configure_multiline_editor(self.input_context, height=200)
        layout.addWidget(self.input_context)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Single Character", "Dual Character"])
        self.mode_combo.setCurrentIndex(1)
        self.mode_combo.currentIndexChanged.connect(self._update_character_mode)

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


    def _build_semantic_score(self, parent: QtWidgets.QVBoxLayout) -> None:
        self.semantic_section = CollapsibleSection(
            "SEMANTIC PERFORMANCE TAG", expanded=True
        )
        self.semantic_section.toggled.connect(self._semantic_section_toggled)
        layout = self.semantic_section.body_layout()
        self.score_title_a = QtWidgets.QLabel("PERFORMANCE")
        layout.addWidget(self.score_title_a)
        self.initial_score_title_a = QtWidgets.QLabel("INITIAL PERFORMANCE")
        layout.addWidget(self.initial_score_title_a)
        self.initial_score_editor = QtWidgets.QPlainTextEdit()
        _configure_multiline_editor(self.initial_score_editor, height=42, fixed_height=True)
        self.initial_score_editor.textChanged.connect(self._score_changed)
        layout.addWidget(self.initial_score_editor)
        self.dialogue_score_title_a = QtWidgets.QLabel("DIALOGUE PERFORMANCE")
        layout.addWidget(self.dialogue_score_title_a)
        self.score_editor = QtWidgets.QPlainTextEdit()
        self.score_editor.setPlaceholderText("Generate or load a performance plan to begin editing.")
        _configure_multiline_editor(self.score_editor, height=260)
        self.score_editor.textChanged.connect(self._score_changed)
        layout.addWidget(self.score_editor)
        self.score_title_b = QtWidgets.QLabel("SECOND CHARACTER PERFORMANCE")
        self.score_editor_b = QtWidgets.QPlainTextEdit()
        _configure_multiline_editor(self.score_editor_b, height=260)
        self.score_editor_b.textChanged.connect(self._score_changed)
        self.score_title_b.hide()
        self.score_editor_b.hide()
        self.initial_score_title_b = QtWidgets.QLabel("INITIAL PERFORMANCE")
        self.initial_score_title_b.hide()
        self.initial_score_editor_b = QtWidgets.QPlainTextEdit()
        _configure_multiline_editor(self.initial_score_editor_b, height=42, fixed_height=True)
        self.initial_score_editor_b.textChanged.connect(self._score_changed)
        self.initial_score_editor_b.hide()
        self.dialogue_score_title_b = QtWidgets.QLabel("DIALOGUE PERFORMANCE")
        self.dialogue_score_title_b.hide()
        self._semantic_score_editors = (self.score_editor, self.score_editor_b)
        for editor in self._semantic_score_editors:
            editor.installEventFilter(self)
            editor.viewport().installEventFilter(self)
        layout.addWidget(self.score_title_b)
        layout.addWidget(self.initial_score_title_b)
        layout.addWidget(self.initial_score_editor_b)
        layout.addWidget(self.dialogue_score_title_b)
        layout.addWidget(self.score_editor_b)
        self.score_legend = QtWidgets.QLabel("Current panel character: speaking = yellow; listening = blue; semantic tags = magenta.")
        self.score_legend.hide()
        layout.addWidget(self.score_legend)
        controls = QtWidgets.QHBoxLayout()
        self.validate_score_button = QtWidgets.QPushButton("Validate Tag")
        self.validate_score_button.clicked.connect(self.validate_score)
        controls.addWidget(self.validate_score_button)
        self.apply_score_button = QtWidgets.QPushButton("Apply Tag Edits")
        self.apply_score_button.clicked.connect(self.apply_score_edits)
        self.validate_score_button.setEnabled(False)
        self.apply_score_button.setEnabled(False)
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
        parent.addWidget(self.semantic_section)

    def _build_reason_view(self, parent: QtWidgets.QVBoxLayout) -> None:
        self.interpretation_section = CollapsibleSection(
            "ACTING INTERPRETATION BY PHRASE", expanded=False
        )
        self.interpretation_section.toggled.connect(
            self._interpretation_section_toggled
        )
        layout = self.interpretation_section.body_layout()
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
        parent.addWidget(self.interpretation_section)

    def set_study_ui_mode(self, mode: str) -> None:
        """Apply an internal presentation condition without mutating the plan."""
        normalized = normalize_study_ui_mode(mode)
        if normalized != self.study_ui_mode:
            record_study_ui_mode_change(self._study_ui_session, normalized)
        self.study_ui_mode = normalized
        self._apply_study_ui_mode()

    def _apply_study_ui_mode(self) -> None:
        state = study_ui_section_state(self.study_ui_mode)
        for name, section in (
            ("semantic", self.semantic_section),
            ("interpretation", self.interpretation_section),
        ):
            section.set_expanded(state[name]["expanded"])
            section.setVisible(state[name]["visible"])
        if state["semantic"]["visible"]:
            self._schedule_semantic_score_editor_resize()

    def _semantic_section_toggled(self, expanded: bool) -> None:
        self._record_inspection_event(
            "semantic_section_opened" if expanded else "semantic_section_closed"
        )
        if expanded:
            self._schedule_semantic_score_editor_resize()

    def _interpretation_section_toggled(self, expanded: bool) -> None:
        context = self._current_interpretation_context() if expanded else {}
        self._record_inspection_event(
            "interpretation_section_opened"
            if expanded
            else "interpretation_section_closed",
            **context,
        )

    def _current_interpretation_context(self) -> dict[str, str]:
        if not isinstance(self.score_model, DualSparseScoreModel):
            return {}
        rows = self.score_model.reason_entries()
        index = self.phrase_number.value() - 1
        if not 0 <= index < len(rows):
            return {}
        actor, event = rows[index]
        return {
            "actor": actor,
            "event_id": str(event.get("event_id") or ""),
        }

    def _record_inspection_event(self, event: str, **context: str) -> None:
        sequence_id, run_id = self._study_event_identifiers()
        self._inspection_events.append(
            build_inspection_event(
                event,
                study_ui_mode=self.study_ui_mode,
                sequence_id=sequence_id,
                run_id=run_id,
                **context,
            )
        )
        if self.plan is not None and self.source_path is not None and sequence_id:
            try:
                self._save_authoring_session_for_path(self.source_path)
            except Exception as exc:
                self._append_backend_output(f"Could not save inspection event: {exc}")

    def _record_semantic_edit_event(self) -> None:
        sequence_id, run_id = self._study_event_identifiers()
        self._semantic_edit_events.append(
            build_semantic_edit_event(
                study_ui_mode=self.study_ui_mode,
                sequence_id=sequence_id,
                run_id=run_id,
            )
        )
        if self.plan is not None and self.source_path is not None and sequence_id:
            try:
                self._save_authoring_session_for_path(self.source_path)
            except Exception as exc:
                self._append_backend_output(f"Could not save semantic edit event: {exc}")

    def _study_event_identifiers(self) -> tuple[str, str]:
        sequence_id = str((self.plan or {}).get("sequence_id") or "")
        run_id = ""
        if self.source_path is not None:
            run_id = next(
                (part for part in self.source_path.parts if part.startswith("run_")), ""
            )
        return sequence_id, run_id

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        finish_study_ui_session(self._study_ui_session)
        sequence_id, _run_id = self._study_event_identifiers()
        if self.plan is not None and self.source_path is not None and sequence_id:
            try:
                self._save_authoring_session_for_path(self.source_path)
            except Exception as exc:
                self._append_backend_output(f"Could not close study UI session: {exc}")
        super().closeEvent(event)

    def _build_advanced_tab(self) -> None:
        advanced = QtWidgets.QWidget()
        self.advanced_tab = advanced
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

        self.jali_speech_settings_group = QtWidgets.QGroupBox("JALI Speech Settings")
        jali_settings = QtWidgets.QFormLayout(self.jali_speech_settings_group)
        self.jali_filter_silence_gaps = QtWidgets.QCheckBox("Filter Silence Gaps")
        self.jali_filter_silence_gaps.setChecked(True)
        jali_settings.addRow(self.jali_filter_silence_gaps)
        self.jali_silence_threshold = QtWidgets.QDoubleSpinBox()
        self.jali_silence_threshold.setRange(-100.0, 0.0)
        self.jali_silence_threshold.setDecimals(1)
        self.jali_silence_threshold.setSingleStep(1.0)
        self.jali_silence_threshold.setValue(-35.0)
        self.jali_silence_threshold.setSuffix(" dB")
        jali_settings.addRow("Silence Threshold", self.jali_silence_threshold)
        self.jali_animate_from_scratch = QtWidgets.QCheckBox(
            "Animate from scratch on next Generate"
        )
        jali_settings.addRow(self.jali_animate_from_scratch)
        self.jali_filter_silence_gaps.toggled.connect(
            self.jali_silence_threshold.setEnabled
        )
        self.audio_folder.editingFinished.connect(self._apply_jali_sequence_defaults)
        self.jali_speech_settings_group.setEnabled(
            self.prepare_jali_speech_next.isChecked()
        )
        self.prepare_jali_speech_next.toggled.connect(
            self.jali_speech_settings_group.setEnabled
        )
        layout.addWidget(self.jali_speech_settings_group)

        layout.addWidget(QtWidgets.QLabel("Backend Generation Log"))
        self.backend_log = QtWidgets.QPlainTextEdit()
        _configure_multiline_editor(
            self.backend_log, height=180, read_only=True, fixed_height=True
        )
        self.backend_log.setMaximumBlockCount(500)
        layout.addWidget(self.backend_log)

        layout.addStretch(1)
        self.tabs.addTab(advanced, "Advanced / Debug")

    def _select_audio_folder(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Input Audio Folder", self.audio_folder.text() or str(Path.cwd())
        )
        if path:
            self.audio_folder.setText(path)
            self._apply_jali_sequence_defaults()

    def _apply_jali_sequence_defaults(self) -> None:
        settings = jali_speech_settings_for_audio_folder(self.audio_folder.text())
        self.jali_filter_silence_gaps.setChecked(settings["filter_silence_gaps"])
        self.jali_silence_threshold.setValue(settings["silence_threshold_db"])

    def _jali_speech_settings_data(self) -> dict[str, Any]:
        settings = normalize_jali_speech_settings({
            "filter_silence_gaps": self.jali_filter_silence_gaps.isChecked(),
            "silence_threshold_db": self.jali_silence_threshold.value(),
        })
        return {
            **settings,
            "animate_from_scratch_next": self.jali_animate_from_scratch.isChecked(),
        }

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
        if hasattr(self, "jali_speech_status_labels"):
            self.jali_speech_status_labels["B"].setVisible(dual)
        if hasattr(self, "jali_speech_settings_group"):
            self.jali_speech_settings_group.setVisible(dual)
        if hasattr(self, "prepare_jali_speech_next"):
            self.prepare_jali_speech_next.setVisible(dual)
        if hasattr(self, "legacy_look_at_label"):
            self.legacy_look_at_label.setVisible(not dual)
            for _semantic, _maya, row in self.look_at_rows:
                row.setVisible(not dual)
        if hasattr(self, "gaze_calibration_label"):
            self.gaze_calibration_label.setVisible(dual)
        if dual and hasattr(self, "validation_label"):
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
                self, "Dialogue Required", "Enter the dialogue before generating a performance plan."
            )
            return
        if not character_a or (dual and not character_b):
            QtWidgets.QMessageBox.warning(
                self,
                "Character Mapping Required",
                "Enter both Script Character names before generating."
                if dual else
                "Enter the Script Character name before generating.",
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
        self._generation_had_active_plan = self.plan is not None and self.score_model is not None and self.source_path is not None
        self._pending_animation_mode = "single"
        self._pending_dual_mappings = {}
        self.backend_log.clear()
        self.generate_plan_button.setEnabled(False)
        self.generation_status.setText(
            "Generating performance plan... current plan preserved until replacement succeeds."
            if self._generation_had_active_plan else "Generating performance plan..."
        )
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

    def _invalidate_generated_presentation(self) -> None:
        """Clear stale generated output while preserving all authoring inputs/setup."""
        self.plan = None
        self.score_model = None
        self.source_path = None
        self.current_event_index = None
        self.score_editor.clear()
        self.score_editor_b.clear()
        self.initial_score_editor.clear()
        self.initial_score_editor_b.clear()
        self.score_editor_b.hide()
        self.score_title_b.hide()
        self.initial_score_title_b.hide()
        self.initial_score_editor_b.hide()
        self.dialogue_score_title_b.hide()
        self.score_legend.hide()
        self.phrase_reason.clear()
        self.validation_details.clear()
        self.validation_details.hide()
        self.validation_label.setText("Generating a new plan; previous output invalidated.")

    def _append_backend_output(self, value: str) -> None:
        text = str(value).strip()
        if text:
            self.backend_log.appendPlainText(text)

    def _generation_succeeded(self, plan_path: object) -> None:
        self.generate_plan_button.setEnabled(True)
        path = Path(str(plan_path))
        if self.load_plan(path, preserve_authoring_text=True):
            self._generation_had_active_plan = False
            try:
                if (
                    self.mode_combo.currentIndex() == 1
                    and self.prepare_jali_speech_next.isChecked()
                ):
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
                    issue for issue in self.score_model.validate(self._score_payload()).errors
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
            self._generation_failed("The backend completed, but the generated plan could not be loaded. See Backend Generation Log for the load error.")

    def _generation_failed(self, message: str) -> None:
        self.generate_plan_button.setEnabled(True)
        self.generation_status.setText(
            "Performance plan generation failed â€” previous plan preserved."
            if self._generation_had_active_plan else "Performance plan generation failed."
        )
        self.generation_status.setStyleSheet("color: #9b1c1c;")
        self._generation_had_active_plan = False
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

    def _character_mapping_rows_data(self) -> list[dict[str, str]]:
        return [
            {"script_name": script_field.text().strip(), "maya_node": maya_field.text().strip()}
            for script_field, maya_field, _row in self.character_rows
        ]

    def _rebind_character_rows(self, plan_characters: list[object]) -> None:
        """Keep each Maya rig with its script actor when plan display order changes."""
        rebound = rebind_character_mappings(plan_characters, self._character_mapping_rows_data())
        for index, mapping in enumerate(rebound):
            self.character_rows[index][0].setText(mapping["script_name"])
            self.character_rows[index][1].setText(mapping["maya_node"])

    def _dual_runtime_mappings(self, plan_characters: list[object]) -> dict[str, dict[str, str]]:
        return runtime_character_mappings(plan_characters, self._character_mapping_rows_data())

    def _invalidate_actor_rig_caches(self, mappings: dict[str, dict[str, str]]) -> None:
        """Discard actor-bound data only when that actor has a genuinely new rig."""
        invalidated: list[str] = []
        baseline_actors = (self.jali_base_baseline or {}).get("actors") or {}
        for actor, mapping in mappings.items():
            node = mapping["maya_node"]
            speech = self.jali_speech_bases.get(actor) or {}
            baseline = baseline_actors.get(actor) if isinstance(baseline_actors, dict) else None
            if (speech.get("maya_node") and speech.get("maya_node") != node) or (
                isinstance(baseline, dict) and baseline.get("maya_node") and baseline.get("maya_node") != node
            ):
                self.jali_speech_bases.pop(actor, None)
                self.dual_gaze_baselines.pop(actor, None)
                for key in list(self.dual_gaze_calibrations):
                    if key.startswith(actor + "->"):
                        self.dual_gaze_calibrations.pop(key)
                invalidated.append(actor)
        if invalidated:
            self.jali_base_baseline = None
            self._append_backend_output(
                "Discarded actor-bound JALI/gaze caches after rig change: " + ", ".join(invalidated)
            )

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
                self, "Dialogue Required", "Dialogue is required to compile animation."
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
            self.plan = save_animation_runtime_plan(
                self.score_model,
                self._score_payload(),
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
        self._pending_dual_master_audio = None
        if self.plan is None or self.score_model is None or self.source_path is None or not self.commit_current_event(show_error=True) or not self.validate_score(show_dialog=True): return
        script=self.input_script.toPlainText(); audio=self.audio_folder.text().strip()
        if not script.strip() or not audio:
            QtWidgets.QMessageBox.warning(self,"Animation Setup Incomplete","Dialogue and Input Audio Folder are required."); return
        mappings: dict[str, dict[str, str]]={}; runtime: dict[str, dict[str, str]]={}; self.backend_log.clear()
        self.generate_animation_button.setEnabled(False)
        self.restore_jali_base_button.setEnabled(False)
        self.animation_status.setText("Preflighting master audio...")
        self.animation_status.setStyleSheet("color: #1d4ed8;")
        QtCore.QCoreApplication.processEvents()
        stage = "Preflighting master audio"
        try:
            plan_characters = self.plan.get("characters", [])
            if not isinstance(plan_characters, list) or len(plan_characters) != 2:
                raise RuntimeError("Dual Performance Plan requires two named characters.")
            fps = current_scene_fps()
            master_wav = resolve_dual_master_wav(audio, plan_characters)
            self._pending_dual_master_audio = master_audio_timeline_info(
                master_wav, fps
            )
            self._append_backend_output(
                "Master audio preflight: "
                f"{self._pending_dual_master_audio['path']}; "
                f"seconds={self._pending_dual_master_audio['seconds']:.3f}; "
                f"fps={self._pending_dual_master_audio['fps']:g}; "
                f"end_frame={self._pending_dual_master_audio['end_frame']}"
            )
            mappings = self._dual_runtime_mappings(plan_characters)
            for actor, mapping in mappings.items():
                if not mapping["maya_node"] or not cmds.objExists(mapping["maya_node"]):
                    raise RuntimeError(f"{actor}: valid script character and Maya rig mapping are required.")
            self._invalidate_actor_rig_caches(mappings)
            prepare_requested = self.prepare_jali_speech_next.isChecked()
            force_from_scratch = False
            effective_jali_settings: dict[str, Any] | None = None
            if prepare_requested:
                stage = "Preparing native JALI speech"
                self.animation_status.setText("Preparing native JALI speech...")
                QtCore.QCoreApplication.processEvents()
                source_transcripts = export_dual_source_transcripts(
                    script=script, audio_folder=audio, characters=plan_characters
                )
                ui_jali_settings = self._jali_speech_settings_data()
                force_from_scratch = ui_jali_settings["animate_from_scratch_next"]
                effective_jali_settings = {
                    key: ui_jali_settings[key]
                    for key in ("filter_silence_gaps", "silence_threshold_db")
                }
                prepared = ensure_dual_jali_speech_bases(
                    actors=plan_characters,
                    character_mappings=mappings,
                    source_transcripts=source_transcripts,
                    saved_metadata=self.jali_speech_bases,
                    jali_settings=effective_jali_settings,
                    force_from_scratch=force_from_scratch,
                    cmds_module=cmds,
                    mel_module=mel,
                    status_callback=self._set_jali_speech_status,
                )
            else:
                stage = "Resolving existing JALI speech"
                self.animation_status.setText("Using existing JALI speech...")
                QtCore.QCoreApplication.processEvents()
                prepared = {}
                for actor in plan_characters:
                    wav = resolve_character_wav(audio, actor)
                    row = resolve_existing_jali_speech_base(
                        actor=actor,
                        script_name=mappings[actor]["script_name"],
                        maya_node=mappings[actor]["maya_node"],
                        wav_path=wav,
                        cmds_module=cmds,
                    )
                    prepared[actor] = row
                    self._set_jali_speech_status(
                        actor, row["sound_file"], "existing"
                    )
            prior_baseline = self.jali_base_baseline
            if any(row["preparation_status"] == "prepared" for row in prepared.values()):
                self.jali_base_baseline = None
            elif isinstance(prior_baseline, dict):
                baseline_actors = prior_baseline.get("actors") or {}
                if any(
                    not isinstance(baseline_actors.get(actor), dict)
                    or baseline_actors[actor].get("jsync") != prepared[actor]["jsync"]
                    or baseline_actors[actor].get("sound_file") != prepared[actor]["sound_file"]
                    for actor in plan_characters
                ):
                    self.jali_base_baseline = None
            if prepare_requested:
                self.jali_speech_bases = prepared
                self.prepare_jali_speech_next.setChecked(False)
                self.jali_animate_from_scratch.setChecked(False)
                self._save_authoring_session_for_path(self.source_path)
            for actor in plan_characters:
                row = prepared[actor]
                mappings[actor] = {
                    "script_name": row["script_name"], "maya_node": row["maya_node"],
                    "sound_file": row["sound_file"], "transcript_path": row["txt_path"],
                    "jsync": row["jsync"],
                }
                runtime[actor] = {
                    "script_name": row["script_name"], "sound_file": row["sound_file"],
                    "transcript_path": row["txt_path"],
                }
                if prepare_requested:
                    actual = row["actual_jali_settings"]
                    self._append_backend_output(
                        f"{actor}: JALI speech base {row['preparation_status']}; rig={row['maya_node']}; "
                        f"jSync={row['jsync']}; sound_file={row['sound_file']}; transcript={row['txt_path']}; "
                        f"txt_sha256={row['txt_sha256']}; alignment={row['alignment_status']}; "
                        f"requested_filter={effective_jali_settings['filter_silence_gaps']}; "
                        f"requested_threshold_db={effective_jali_settings['silence_threshold_db']:g}; "
                        f"actual_filter={actual['filter_silence_gaps']}; "
                        f"actual_threshold_db={actual['silence_threshold_db']:g}; "
                        f"speech_style={row['speech_style']}; "
                        f"from_scratch={'yes' if force_from_scratch else 'no'}"
                    )
                else:
                    self._append_backend_output(
                        f"{actor}: overlay-only using existing JALI base; rig={row['maya_node']}; "
                        f"jSync={row['jsync']}; sound_file={row['sound_file']}; "
                        f"transcript={row['txt_path']}; JALI preparation skipped"
                    )
            # Native JALI bases for BOTH actors are now verified. Capture the
            # immutable baseline before any backend compile or semantic overlay.
            self.jali_base_baseline = capture_dual_jali_base_if_absent(
                self.jali_base_baseline, character_mappings=mappings
            )
            self._save_authoring_session_for_path(self.source_path)
            stage = "Compiling Performance Plan"
            self.animation_status.setText("Compiling Performance Plan...")
            QtCore.QCoreApplication.processEvents()
            animation_dir=self.source_path.parent/"animation"
            candidate_plan = self.score_model.apply(self._score_payload())
            if candidate_plan.get("schema_version") == "dual_performance_plan_v2":
                loaded_content = self._loaded_dual_snapshot_content or canonical_dual_authored_content(self.plan)
                if is_dual_plan_changed_from_loaded_snapshot(candidate_plan, loaded_content):
                    runtime_plan = default_edited_path(self.source_path)
                    save_performance_plan(candidate_plan, runtime_plan)
                    self.source_path = runtime_plan
                    self._loaded_dual_snapshot_content = canonical_dual_authored_content(candidate_plan)
                else:
                    runtime_plan = self.source_path
                self.plan = candidate_plan
            else:
                runtime_plan = default_edited_path(self.source_path) if is_dual_plan_edited(candidate_plan, self.score_model.original_plan) else self.source_path
                self.plan = save_animation_runtime_plan(self.score_model, self._score_payload(), runtime_plan)
            self._save_authoring_session_for_path(self.source_path)
            self._pending_animation_mode="dual_emotion_only"; self._pending_dual_mappings=mappings
            command=self.animation_runner.start_dual(performance_plan=runtime_plan,script=script,audio_folder=audio,output_dir=animation_dir,fps=fps,runtime_mapping=runtime)
            self._append_backend_output(f"Dual emotion-only output: {command.output_dir}")
        except Exception as exc: self._animation_failed(f"{stage} failed: {exc}")

    def _jali_speech_status_label(self, actor: str) -> QtWidgets.QLabel | None:
        names = [row[0].text().strip() for row in self.character_rows]
        index = next((i for i, name in enumerate(names) if name.casefold() == str(actor).casefold()), None)
        return self.jali_speech_status_labels.get("A" if index == 0 else "B" if index == 1 else "")

    def _set_jali_speech_status(self, actor: str, clip: str, status: str) -> None:
        label = self._jali_speech_status_label(actor)
        if label is not None:
            label.setText(speech_base_status_text(actor, clip, status))
            label.setStyleSheet(
                "color: #166534;" if status in {"reused", "prepared"}
                else "color: #9b1c1c;" if status == "failed"
                else "color: #1d4ed8;" if status == "preparing"
                else ""
            )
            QtCore.QCoreApplication.processEvents()

    def _refresh_jali_speech_status_preview(self, *_args: object) -> None:
        if not hasattr(self, "jali_speech_status_labels"):
            return
        folder = self.audio_folder.text().strip() if hasattr(self, "audio_folder") else ""
        for index, alias in enumerate(("A", "B")):
            actor = self.character_rows[index][0].text().strip() or alias
            clip = ""
            try:
                if folder:
                    clip = resolve_character_wav(folder, actor).stem
            except Exception:
                pass
            status = (
                "will_prepare"
                if self.prepare_jali_speech_next.isChecked()
                else "existing_required"
            )
            self.jali_speech_status_labels[alias].setText(
                speech_base_status_text(actor, clip, status)
            )
            self.jali_speech_status_labels[alias].setStyleSheet("")

    def _animation_compile_succeeded(self, manifest_path: object) -> None:
        stream = io.StringIO()
        try:
            with redirect_stdout(stream), redirect_stderr(stream):
                if self._pending_animation_mode == "dual_emotion_only":
                    self.animation_status.setText("Applying semantic animation...")
                    QtCore.QCoreApplication.processEvents()
                    gaze_mappings = {alias: {**row, "gaze_targets": {key.split("->", 1)[1]: value for key, value in self.dual_gaze_calibrations.items() if key.startswith(alias + "->")}} for alias, row in self._pending_dual_mappings.items()}
                    is_canonical_dual_plan = (self.plan or {}).get("schema_version") == "dual_performance_plan_v2"
                    if is_canonical_dual_plan:
                        # Every canonical-dual prepare call is read-only. Complete both-character
                        # preflight before speaker realignment mutates either rig.
                        listener_context = prepare_legacy_dual_listener_mask_artifacts(manifest_path=Path(str(manifest_path)), character_mappings=self._pending_dual_mappings)
                        gaze_context = prepare_dual_gaze_artifacts(manifest_path=Path(str(manifest_path)), character_mappings=gaze_mappings)
                        overlay_context = prepare_dual_head_blink_overlays(manifest_path=Path(str(manifest_path)), character_mappings=self._pending_dual_mappings, baseline=self.jali_base_baseline or {})
                    else:
                        listener_context = prepare_dual_listener_mask_artifacts(manifest_path=Path(str(manifest_path)), character_mappings=self._pending_dual_mappings)
                        gaze_context = prepare_dual_gaze_only_artifacts(manifest_path=Path(str(manifest_path)), character_mappings=gaze_mappings)
                        overlay_context = None
                    result=apply_dual_speaker_emotion_artifacts(manifest_path=Path(str(manifest_path)), character_mappings=self._pending_dual_mappings)
                    listener_result = apply_dual_listener_mask_artifacts(prepared_context=listener_context)
                    gaze_result = apply_dual_gaze_only_artifacts(prepared_context=gaze_context)
                    for warning in gaze_context.get("warnings", []):
                        self._append_backend_output(f"Gaze timing warning: {warning}")
                    overlay_result = apply_dual_head_blink_overlays(prepared_context=overlay_context) if overlay_context else {}
                    blink_diagnostic = diagnose_blink_ownership(prepared_context=overlay_context) if overlay_context else None
                    for actor, item in result.items(): self._append_backend_output(f"{actor}: jSync={item['jsync_node']}; staging={item['staging_dir']}; mask_tags={item['mask_tag_count']}; realign={'completed' if item['realign_completed'] else 'failed'}; realign_filter={item['jali_settings']['filter_silence_gaps']}; realign_threshold_db={item['jali_settings']['silence_threshold_db']:g}; calculate_paralinguals={item['calculate_paralinguals']}; calculate_blinks={item['calculate_blinks']}; paths_restored={'yes' if item['paths_restored'] else 'no'}; mask_binding={'applied' if item['mask_binding'] else 'skipped'}")
                    for actor in self.plan.get("characters", []):
                        item = listener_result[actor]
                        self._append_backend_output(f"{actor}: listener_mask_events={item['listener_mask_events']}; managed_user_plugs={len(item['managed_user_plugs'])}; FACS_animationSource=Add")
                    if is_canonical_dual_plan and listener_context.get("expressive_eyelid_mapping_requirement"):
                        self._append_backend_output("Expressive eyelid Maya-smoke requirement (no guessed User mapping): " + ", ".join(listener_context["expressive_eyelid_mapping_requirement"]))
                    gaze_events = sum(gaze_result[actor]['gaze_events'] for actor in self.plan.get("characters", []))
                    overlay_summary = f"; additive head/blink overlays ({sum(overlay_result[actor]['head_key_count'] + overlay_result[actor]['blink_key_count'] for actor in self.plan.get('characters', []))} keys)" if overlay_result else ""
                    self._append_backend_output(f"Applied: native speaker Mask; listener User Mask reactions; calibrated gaze ({gaze_events} events){overlay_summary}\njSync preserved: yes")
                    if blink_diagnostic:
                        self._append_backend_output("Blink ownership diagnostic: JALI calculate_blinks=False; native JALI eyelid/paralingual curves allowed; User performative blink controls owned by JALITEST blink layer.")
                    if self._pending_dual_master_audio is None:
                        raise RuntimeError("Dual master audio preflight state is missing.")
                    timeline_audio = apply_master_audio_to_maya_timeline(
                        self._pending_dual_master_audio["path"],
                        self._pending_dual_master_audio["fps"],
                        cmds_module=cmds,
                        mel_module=mel,
                    )
                    self._append_backend_output(
                        "Master timeline audio: "
                        f"path={timeline_audio['path']}; "
                        f"seconds={timeline_audio['seconds']:.3f}; "
                        f"fps={timeline_audio['fps']:g}; "
                        f"end_frame={timeline_audio['end_frame']}; "
                        f"audio_node={timeline_audio['audio_node']}; "
                        f"node_action={'reused' if timeline_audio['audio_node_reused'] else 'created'}"
                    )
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
        self.restore_jali_base_button.setEnabled(True)
        self.animation_status.setText("Dual performance animation applied." if self._pending_animation_mode == "dual_emotion_only" else "Animation generated.")
        self.animation_status.setStyleSheet("color: #166534;")
        self._pending_animation_mode = "single"; self._pending_dual_mappings = {}; self._pending_dual_master_audio = None
        QtWidgets.QMessageBox.information(
            self, "Animation Generated", "Animation artifacts were compiled and applied in Maya."
        )

    def _animation_failed(self, message: str) -> None:
        self._pending_animation_mode = "single"; self._pending_dual_mappings = {}; self._pending_dual_master_audio = None
        self.generate_animation_button.setEnabled(True)
        self.restore_jali_base_button.setEnabled(True)
        self.animation_status.setText("Animation generation failed.")
        self.animation_status.setStyleSheet("color: #9b1c1c;")
        self._append_backend_output(message)
        lines = [line.strip() for line in str(message).splitlines() if line.strip()]
        QtWidgets.QMessageBox.critical(
            self,
            "Could Not Generate Animation",
            lines[-1] if lines else "Unknown animation error.",
        )

    def _restore_jali_base(self) -> None:
        """Restore the captured live JALI base without compiling a new plan."""
        if not self.jali_base_baseline:
            QtWidgets.QMessageBox.warning(self, "Restore JALI Base", "No pre-JALITEST dual baseline has been captured yet.")
            return
        plan_characters = (self.plan or {}).get("characters")
        if isinstance(plan_characters, list):
            actors = plan_characters
        elif isinstance(plan_characters, dict):
            actors = list(plan_characters)
        else:
            QtWidgets.QMessageBox.warning(self, "Restore JALI Base", "The loaded plan has no valid dual character mapping.")
            return
        try:
            mappings = self._dual_runtime_mappings(actors)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Restore JALI Base", str(exc))
            return
        self.generate_animation_button.setEnabled(False); self.restore_jali_base_button.setEnabled(False)
        self.animation_status.setText("Restoring JALI Base..."); self.animation_status.setStyleSheet("color: #1d4ed8;")
        stream = io.StringIO()
        try:
            with redirect_stdout(stream), redirect_stderr(stream):
                result = restore_dual_jali_base(baseline=self.jali_base_baseline, character_mappings=mappings)
        except Exception as exc:
            self._append_backend_output(stream.getvalue())
            self.generate_animation_button.setEnabled(True); self.restore_jali_base_button.setEnabled(True)
            self.animation_status.setText("Restore JALI Base failed."); self.animation_status.setStyleSheet("color: #9b1c1c;")
            QtWidgets.QMessageBox.critical(self, "Could Not Restore JALI Base", str(exc))
            return
        self._append_backend_output(stream.getvalue())
        self._append_backend_output("Restored JALI Base")
        for actor in result["restored"]: self._append_backend_output(f"{actor}: restored")
        for warning in result.get("warnings", []): self._append_backend_output(f"Restore warning: {warning}")
        overlay_label = "listener/gaze/head/blink" if (self.plan or {}).get("schema_version") == "dual_performance_plan_v2" else "listener/gaze"
        self._append_backend_output(f"Removed JALITEST {overlay_label} overlays")
        self._append_backend_output("jSync preserved: yes")
        self.generate_animation_button.setEnabled(True); self.restore_jali_base_button.setEnabled(True)
        if result.get("warnings"):
            self.animation_status.setText("JALI Base restored with gaze-neutral warning; see Backend Generation Log."); self.animation_status.setStyleSheet("color: #92400e;")
        else:
            self.animation_status.setText("JALI Base restored."); self.animation_status.setStyleSheet("color: #166534;")
        QtWidgets.QMessageBox.information(self, "Restored JALI Base", "The captured JALI base was restored for both actors.")

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
        if self.plan.get("schema_version") in {"dual_performance_plan_v0", "dual_performance_plan_v1", "dual_performance_plan_v2"}:
            self._clear_look_at_targets()
            self.legacy_look_at_label.hide()
            while self.gaze_calibration_layout.count():
                item=self.gaze_calibration_layout.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            names = self.plan.get("characters") or {}
            # Snapshot automatic baselines while the calibration UI is built;
            # artists only author semantic target poses.
            actors = names if isinstance(names, list) else list(names)
            for actor in actors:
                self._capture_dual_baseline(actor)
            self.gaze_calibration_layout.addWidget(QtWidgets.QLabel("Required by Current Plan"))
            for actor, target in required_calibration_pairs(self.plan):
                row=QtWidgets.QWidget(); layout=QtWidgets.QHBoxLayout(row); layout.setContentsMargins(0,0,0,0)
                layout.addWidget(QtWidgets.QLabel(display_target(actor, names))); layout.addWidget(QtWidgets.QLabel("→")); layout.addWidget(QtWidgets.QLabel(display_target(target, names)))
                button=QtWidgets.QPushButton("Capture Look-at")
                button.clicked.connect(lambda _checked=False,a=actor,t=target: self._capture_dual_look_at(a,t))
                layout.addWidget(button); self.gaze_calibration_layout.addWidget(row)
            return
        self.legacy_look_at_label.show()
        rows = refresh_look_at_mappings(
            required_look_at_targets(self.plan), self._look_at_mapping_data()
        )
        self._clear_look_at_targets()
        for mapping in rows:
            self._add_look_at_target(mapping["semantic_target"])
            self.look_at_rows[-1][1].setText(mapping["maya_node"])

    def _capture_dual_look_at(self, actor: str, target: str) -> None:
        try:
            node = self._dual_runtime_mappings([actor])[actor]["maya_node"]
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Look-at Capture", str(exc)); return
        eye=qualify_rig_control(node, "eyeStare_world"); both=qualify_rig_control(node, "CNT_BOTH_EYES")
        if not node or not cmds.objExists(eye) or not cmds.objExists(both):
            QtWidgets.QMessageBox.warning(self, "Look-at Capture", f"{actor} needs a mapped rig with eyeStare_world and CNT_BOTH_EYES."); return
        baseline = self.dual_gaze_baselines.get(actor) or self._capture_dual_baseline(actor)
        if not baseline:
            return
        self.dual_gaze_calibrations[calibration_key(actor,target)] = capture_target_pose_and_restore(
            eye, both, baseline_translate_z=float(baseline["baseline_translateZ"]),
            both_eyes_translate=baseline["both_eyes_translate"], cmds_module=cmds,
        )
        names=(self.plan or {}).get("characters") or {}; self._append_backend_output(f"Captured look-at: {display_target(actor,names)} -> {display_target(target,names)}")

    def _capture_optional_dual_look_at(self) -> None:
        actor = self.optional_gaze_actor.currentText().strip()
        target = self.optional_gaze_target.currentText().strip().upper()
        error = optional_look_at_validation_error(actor, target)
        if error:
            QtWidgets.QMessageBox.warning(self, "Look-at Capture", error)
            return
        self._capture_dual_look_at(actor, target)

    def _capture_dual_baseline(self, actor: str) -> dict[str, object] | None:
        try:
            node = self._dual_runtime_mappings([actor])[actor]["maya_node"]
        except ValueError:
            return None
        eye=qualify_rig_control(node, "eyeStare_world"); both=qualify_rig_control(node, "CNT_BOTH_EYES")
        if not node or not cmds.objExists(eye) or not cmds.objExists(both):
            return None
        result: dict[str, object] = {"baseline_translateZ": float(cmds.getAttr(eye + ".translateZ")), "both_eyes_translate": [0.0, 0.0]}
        self.dual_gaze_baselines[actor] = result
        return result

    def _restore_authoring_session(
        self, session: dict[str, Any], *, preserve_authoring_text: bool = False,
        plan_characters: list[object] | None = None,
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
        saved_jali_settings = session.get("jali_speech_settings")
        if isinstance(saved_jali_settings, dict):
            normalized = normalize_jali_speech_settings(saved_jali_settings)
            self.jali_filter_silence_gaps.setChecked(normalized["filter_silence_gaps"])
            self.jali_silence_threshold.setValue(normalized["silence_threshold_db"])
            self.jali_animate_from_scratch.setChecked(
                bool(saved_jali_settings.get("animate_from_scratch_next", False))
            )
        else:
            self._apply_jali_sequence_defaults()
        # Legacy lists contain world positions only and must be recaptured.
        self.dual_gaze_calibrations = {str(key): dict(value) for key, value in (session.get("gaze_calibrations") or {}).items() if isinstance(value, dict) and isinstance(value.get("eye_stare_translate"), (list, tuple)) and len(value["eye_stare_translate"]) == 3}
        self.dual_gaze_baselines = {}
        saved_baseline = session.get("jali_base_baseline")
        if isinstance(saved_baseline, dict) and saved_baseline.get("schema_version") == "dual_jali_base_v2":
            self.jali_base_baseline = dict(saved_baseline)
        else:
            self.jali_base_baseline = None
            if saved_baseline is not None:
                self._append_backend_output("Discarded legacy JALI baseline; a fresh JALI baseline will be captured on Generate.")
        saved_speech_bases = session.get("jali_speech_bases")
        self.jali_speech_bases = {
            str(actor): dict(row)
            for actor, row in (saved_speech_bases or {}).items()
            if isinstance(row, dict)
        } if isinstance(saved_speech_bases, dict) else {}
        for script_field, maya_field, _row in self.character_rows:
            script_field.clear()
            maya_field.clear()
        missing_nodes: list[str] = []
        saved_characters = session.get("characters", [])
        restored_characters = (
            rebind_character_mappings(plan_characters, saved_characters)
            if plan_characters is not None else saved_characters
        )
        for index, mapping in enumerate(restored_characters):
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
        sequence_id, run_id = self._study_event_identifiers()
        current_study_ui_session = {
            **self._study_ui_session,
            **({"sequence_id": sequence_id} if sequence_id else {}),
            **({"run_id": run_id} if run_id else {}),
        }
        prior_study_ui_sessions = list(
            (self.authoring_session or {}).get("study_ui_sessions", [])
        )
        study_ui_sessions = [
            session
            for session in prior_study_ui_sessions
            if session.get("started_at") != current_study_ui_session["started_at"]
        ]
        study_ui_sessions.append(current_study_ui_session)
        return build_authoring_session(
            sequence_id=str((self.plan or {}).get("sequence_id") or ""),
            mode="dual" if dual else "single",
            audio_folder=self.audio_folder.text(),
            input_script=self.input_script.toPlainText(),
            input_context=self.input_context.toPlainText(),
            characters=characters,
            look_at_targets=self._look_at_mapping_data(),
            base={
                **{
                    key: value
                    for key, value in (self.authoring_session or {}).items()
                    if key != "gaze_neutrals"
                },
                "gaze_calibrations": self.dual_gaze_calibrations,
                "jali_base_baseline": self.jali_base_baseline,
                "jali_speech_bases": self.jali_speech_bases,
                "jali_speech_settings": self._jali_speech_settings_data(),
                "inspection_events": list(self._inspection_events),
                "semantic_edit_events": list(self._semantic_edit_events),
                "study_ui_sessions": study_ui_sessions,
            },
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
        self._schedule_semantic_score_editor_resize()
        if self._suppress_score_dirty_tracking or self._building or self.score_model is None:
            return
        if score_text_matches_clean_baseline(
            self._score_payload(), self._clean_score_baseline
        ):
            self._semantic_edit_active = False
            self.validation_label.setText("No pending tag edits.")
            self.validation_label.setStyleSheet("color: #166534;")
            self.validation_details.clear()
            self.validation_details.hide()
            self.apply_score_button.setEnabled(False)
            return
        if not self._semantic_edit_active:
            self._semantic_edit_active = True
            self._record_semantic_edit_event()
        if self.score_model is not None:
            self.validation_label.setText("Tag changed — validate before saving.")
            self.validation_label.setStyleSheet("color: #92400e;")
            self.apply_score_button.setEnabled(True)

    def _set_score_editor_text(self, editor: QtWidgets.QPlainTextEdit, text: str) -> None:
        self._suppress_score_dirty_tracking = True
        try:
            editor.setPlainText(text)
        finally:
            self._suppress_score_dirty_tracking = False
        if editor in self._semantic_score_editors:
            _resize_semantic_score_editor(editor)
            self._schedule_semantic_score_editor_resize()

    def _schedule_semantic_score_editor_resize(self) -> None:
        if self._score_resize_scheduled:
            return
        self._score_resize_scheduled = True
        QtCore.QTimer.singleShot(0, self._resize_semantic_score_editors)

    def _resize_semantic_score_editors(self) -> None:
        self._score_resize_scheduled = False
        for editor in self._semantic_score_editors:
            _resize_semantic_score_editor(editor)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        score_widgets = (*self._semantic_score_editors, *(editor.viewport() for editor in self._semantic_score_editors))
        if watched in score_widgets and event.type() == QtCore.QEvent.Type.Resize:
            self._schedule_semantic_score_editor_resize()
        return super().eventFilter(watched, event)

    def _mark_score_editors_clean(self) -> None:
        """Record the currently rendered score as the accepted editor baseline."""
        self._clean_score_baseline = self._score_payload()
        self._semantic_edit_active = False
        self.validation_label.setText("No pending tag edits.")
        self.validation_label.setStyleSheet("color: #166534;")
        self.validation_details.clear()
        self.validation_details.hide()
        self.validate_score_button.setEnabled(self.score_model is not None)
        self.apply_score_button.setEnabled(False)

    def validate_score(self, *, show_dialog: bool = False) -> bool:
        if self.score_model is None:
            if show_dialog:
                QtWidgets.QMessageBox.information(self, "No Plan", "Load a Performance Plan first.")
            return False
        self.score_model.targets.update(target.upper() for target in self._known_look_targets())
        result = self.score_model.validate(self._score_payload())
        if result.valid:
            noun = "changes" if isinstance(self.score_model, DualSparseScoreModel) else "phrases"
            self.validation_label.setText(f"Valid tag — {len(result.phrases)} {noun}")
            self.validation_label.setStyleSheet("color: #166534;")
            self.validation_details.clear()
            self.validation_details.hide()
            return True
        details = "\n".join(str(error) for error in result.errors)
        self.validation_label.setText(f"Invalid tag — {len(result.errors)} error(s)")
        self.validation_label.setStyleSheet("color: #9b1c1c;")
        self.validation_details.setPlainText(details)
        self.validation_details.show()
        if show_dialog:
            QtWidgets.QMessageBox.warning(self, "Invalid Semantic Performance Tag", details)
        return False

    def apply_score_edits(self, *, show_success: bool = True) -> bool:
        if not self.validate_score(show_dialog=True) or self.score_model is None:
            return False
        self.plan = self.score_model.apply(self._score_payload())
        self._refresh_required_look_at_targets()
        self._refresh_phrase_reason()
        self._refresh_metadata_and_diagnostics()
        self._mark_score_editors_clean()
        if show_success:
            QtWidgets.QMessageBox.information(
                self, "Tag Applied", "Valid semantic tag edits were applied to the canonical Performance Plan."
            )
        return True

    def _score_payload(self) -> str | dict[str, str]:
        if isinstance(self.score_model, DualSparseScoreModel):
            return {
                self.score_model.characters[0]: {"initial": self.initial_score_editor.toPlainText(), "dialogue": self.score_editor.toPlainText()},
                self.score_model.characters[1]: {"initial": self.initial_score_editor_b.toPlainText(), "dialogue": self.score_editor_b.toPlainText()},
            }
        return self.score_editor.toPlainText()

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

    def load_plan(self, path: Path, *, preserve_authoring_text: bool = False) -> bool:
        # Native JALI preparation is deliberately a one-shot, opt-in action and
        # is never carried across plan loads or authoring sessions.
        self.prepare_jali_speech_next.setChecked(False)
        try:
            loaded_plan = load_performance_plan(path)
        except Exception as exc:
            self._append_backend_output(f"Could Not Load Plan: {exc}")
            QtWidgets.QMessageBox.critical(self, "Could Not Load Plan", str(exc))
            return False

        self.authoring_session = None
        self.jali_speech_bases = {}
        try:
            sequence_id = str(loaded_plan.get("sequence_id") or "")
            session_path = default_authoring_session_path(path, sequence_id)
            self.authoring_session = (
                load_authoring_session(session_path) if session_path.exists() else None
            )
            if self.authoring_session is not None:
                loaded_inspection_events = self.authoring_session.get(
                    "inspection_events", []
                )
                self._inspection_events = [
                    *loaded_inspection_events,
                    *[
                        event
                        for event in self._inspection_events
                        if event not in loaded_inspection_events
                    ],
                ]
                loaded_semantic_edit_events = self.authoring_session.get(
                    "semantic_edit_events", []
                )
                self._semantic_edit_events = [
                    *loaded_semantic_edit_events,
                    *[
                        event
                        for event in self._semantic_edit_events
                        if event not in loaded_semantic_edit_events
                    ],
                ]
                plan_characters = loaded_plan.get("characters", [])
                self._restore_authoring_session(
                    self.authoring_session,
                    preserve_authoring_text=preserve_authoring_text,
                    plan_characters=plan_characters if isinstance(plan_characters, list) else None,
                )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "Could Not Restore Authoring Session",
                f"The Performance Plan will still load, but its authoring-session sidecar could not be restored.\n\n{exc}",
            )
            self.authoring_session = None

        try:
            if loaded_plan.get("schema_version") == "dual_performance_plan_v2":
                self.mode_combo.setCurrentIndex(1)
                self._update_character_mode()
                characters = loaded_plan.get("characters", [])
                script_path = path.parent / "input_script.txt"
                current_script = self.input_script.toPlainText()
                script_text = current_script if preserve_authoring_text and current_script.strip() else (
                    script_path.read_text(encoding="utf-8") if script_path.exists() else current_script
                )
                anchor_model = build_conversation_anchor_model(
                    script_text, character_a=str(characters[0]), character_b=str(characters[1])
                )
                self.score_model = DualSparseScoreModel(loaded_plan, anchor_model)
            elif loaded_plan.get("schema_version") in {"dual_performance_plan_v0", "dual_performance_plan_v1"}:
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
            self._append_backend_output(f"Could Not Load Plan: {exc}")
            QtWidgets.QMessageBox.critical(self, "Could Not Load Plan", str(exc))
            return False

        self.source_path = path
        self._loaded_dual_snapshot_content = (
            canonical_dual_authored_content(loaded_plan)
            if loaded_plan.get("schema_version") == "dual_performance_plan_v2" else None
        )
        self.current_event_index = None
        self._building = True
        if self.plan.get("schema_version") in {"dual_performance_plan_v1", "dual_performance_plan_v2"}:
            plan_characters = self.plan.get("characters", [])
            if isinstance(plan_characters, list) and len(plan_characters) == 2:
                self._rebind_character_rows(plan_characters)
        events = [event for event in self.plan.get("events", []) if isinstance(event, dict)]
        dual_phrases = [
            phrase for phrase in self.plan.get("phrases", []) if isinstance(phrase, dict)
        ]
        if (
            not preserve_authoring_text
            and (not self.authoring_session or not self.authoring_session.get("input_script"))
        ):
            if isinstance(self.score_model, DualSparseScoreModel):
                self.input_script.setPlainText(script_text)
            else:
                source_rows = events or dual_phrases
                self.input_script.setPlainText(" ".join(
                    str(row.get("span", {}).get("text") or "") for row in source_rows
                ).strip())
        self._set_score_editor_text(self.score_editor, self.score_model.score_text)
        if isinstance(self.score_model, DualSparseScoreModel):
            first, second = self.score_model.characters
            self.optional_gaze_actor.clear(); self.optional_gaze_actor.addItems([first, second])
            self.optional_gaze_target.clear(); self.optional_gaze_target.addItems(list(self.plan.get("gaze_target_candidates") or []))
            self.score_title_a.setText(f"{first} PERFORMANCE")
            self.initial_score_title_a.show()
            self.initial_score_editor.show()
            self.dialogue_score_title_a.show()
            self.score_title_b.setText(f"{second} PERFORMANCE")
            self._set_score_editor_text(self.initial_score_editor, self.score_model.initial_score_texts[first])
            self._set_score_editor_text(self.score_editor_b, self.score_model.score_texts[second])
            self._set_score_editor_text(self.initial_score_editor_b, self.score_model.initial_score_texts[second])
            self.score_title_b.show()
            self.initial_score_title_b.show()
            self.initial_score_editor_b.show()
            self.dialogue_score_title_b.show()
            self.score_editor_b.show()
            self.score_legend.show()
            self._resize_semantic_score_editors()
            self._schedule_semantic_score_editor_resize()
            self._score_highlighters = [
                _SparseScoreHighlighter(self.score_editor.document(), self.score_model.projection, self.score_model.characters, panel_actor=first),
                _SparseScoreHighlighter(self.score_editor_b.document(), self.score_model.projection, self.score_model.characters, panel_actor=second),
            ]
        else:
            self.score_title_a.setText("PERFORMANCE")
            self.initial_score_title_a.hide()
            self.initial_score_editor.hide()
            self.dialogue_score_title_a.hide()
            self.score_title_b.hide()
            self.score_editor_b.hide()
            self.initial_score_title_b.hide()
            self.initial_score_editor_b.hide()
            self.dialogue_score_title_b.hide()
            self.score_legend.hide()
        target_character = str(self.plan.get("target_character") or "")
        if target_character and self.authoring_session is None:
            self.character_rows[0][0].setText(target_character)
        if self.plan.get("schema_version") == "dual_performance_plan_v0" and self.authoring_session is None:
            characters = self.plan.get("characters", {})
            if isinstance(characters, dict):
                self.character_rows[0][0].setText(str(characters.get("A") or ""))
                self.character_rows[1][0].setText(str(characters.get("B") or ""))
        reason_count = len(self.score_model.reason_entries()) if isinstance(self.score_model, DualSparseScoreModel) else len(self.score_model.phrases)
        self.phrase_number.setMaximum(max(1, reason_count))
        self.phrase_number.setValue(1)
        self._building = False
        self.validate_score()
        self._refresh_required_look_at_targets()
        self._refresh_phrase_reason()
        self._refresh_metadata_and_diagnostics()
        self._mark_score_editors_clean()
        return True

    def _refresh_phrase_reason(self) -> None:
        if self.score_model is None:
            self.phrase_reason.setPlainText("Load a Performance Plan to inspect phrase reasons.")
            return
        if isinstance(self.score_model, DualSparseScoreModel):
            self.phrase_reason.setPlainText(
                self.score_model.rationale_view(self.phrase_number.value())
            )
        else:
            self.phrase_reason.setPlainText(
                format_rationale_view(self.score_model, self.phrase_number.value())
            )

    def _refresh_metadata_and_diagnostics(self) -> None:
        if self.plan is None:
            self.metadata_label.setText("No plan loaded")
            return
        characters = self.plan.get("characters")
        character_label = (
            f"characters: {characters}" if isinstance(characters, (dict, list))
            else f"target_character: {self.plan.get('target_character', '')}"
        )
        self.metadata_label.setText(
            "schema_version: {schema}    sequence_id: {sequence}    {character}".format(
                schema=self.plan.get("schema_version", ""),
                sequence=self.plan.get("sequence_id", ""),
                character=character_label,
            )
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
            save_performance_plan(self.plan or {}, path)
            session_path = self._save_authoring_session_for_path(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could Not Save Authoring Data", str(exc))
            return
        self.source_path = path
        if (self.plan or {}).get("schema_version") == "dual_performance_plan_v2":
            self._loaded_dual_snapshot_content = canonical_dual_authored_content(self.plan or {})
        QtWidgets.QMessageBox.information(
            self,
            "Plan Saved",
            f"Saved edited plan:\n{path}\n\nSaved authoring session:\n{session_path}",
        )


def show_performance_plan_editor(
    *, study_ui_mode: str | None = None
) -> PerformancePlanEditor:
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
    PERFORMANCE_PLAN_EDITOR = PerformancePlanEditor(
        parent=maya_main_window(), study_ui_mode=study_ui_mode
    )
    PERFORMANCE_PLAN_EDITOR.show()
    PERFORMANCE_PLAN_EDITOR.raise_()
    PERFORMANCE_PLAN_EDITOR.activateWindow()
    return PERFORMANCE_PLAN_EDITOR
