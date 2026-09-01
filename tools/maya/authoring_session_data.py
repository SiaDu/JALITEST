"""Pure-Python persistence for Maya performance-authoring session mappings."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "authoring_session_v0"
STUDY_UI_NORMAL = "normal"
STUDY_UI_EDITABLE_PLAN = "editable_plan"
STUDY_UI_DIRECT_GENERATION = "direct_generation"
STUDY_UI_MODES = (
    STUDY_UI_NORMAL,
    STUDY_UI_EDITABLE_PLAN,
    STUDY_UI_DIRECT_GENERATION,
)
INSPECTION_EVENT_NAMES = {
    "semantic_section_opened",
    "semantic_section_closed",
    "interpretation_section_opened",
    "interpretation_section_closed",
}
SEMANTIC_EDIT_EVENT_NAMES = {"semantic_edit_started"}


def normalize_study_ui_mode(value: str | None) -> str:
    """Validate an internal study presentation mode without touching plan data."""
    mode = str(value or STUDY_UI_NORMAL).strip().lower()
    if mode not in STUDY_UI_MODES:
        raise ValueError(
            f"Study UI mode must be one of {', '.join(STUDY_UI_MODES)}; got {value!r}."
        )
    return mode


def study_ui_section_state(mode: str) -> dict[str, dict[str, bool]]:
    """Return the programmatic visibility/default expansion for plan sections."""
    normalized = normalize_study_ui_mode(mode)
    visible = normalized != STUDY_UI_DIRECT_GENERATION
    return {
        "semantic": {"visible": visible, "expanded": True},
        "interpretation": {"visible": visible, "expanded": False},
    }


def build_study_ui_session(
    study_ui_mode: str, *, timestamp: str | None = None
) -> dict[str, Any]:
    """Create non-interaction lifecycle metadata for offline duration metrics."""
    mode = normalize_study_ui_mode(study_ui_mode)
    return {
        "started_at": timestamp or datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "study_ui_mode": mode,
        "initial_section_state": study_ui_section_state(mode),
        "mode_changes": [],
    }


def record_study_ui_mode_change(
    session: dict[str, Any], mode: str, *, timestamp: str | None = None
) -> None:
    """Record a programmatic presentation change without creating inspection data."""
    normalized = normalize_study_ui_mode(mode)
    session.setdefault("mode_changes", []).append(
        {
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "study_ui_mode": normalized,
            "section_state": study_ui_section_state(normalized),
        }
    )


def finish_study_ui_session(
    session: dict[str, Any], *, timestamp: str | None = None
) -> None:
    """Close the lifecycle interval without manufacturing a user interaction."""
    if session.get("ended_at") is None:
        session["ended_at"] = timestamp or datetime.now(timezone.utc).isoformat()


def build_inspection_event(
    event: str,
    *,
    study_ui_mode: str,
    timestamp: str | None = None,
    sequence_id: str | None = None,
    run_id: str | None = None,
    actor: str | None = None,
    event_id: str | None = None,
) -> dict[str, str]:
    """Build one sidecar inspection event; semantic edits use a separate path."""
    if event not in INSPECTION_EVENT_NAMES:
        raise ValueError(f"Unsupported inspection event {event!r}.")
    row = {
        "event": event,
        "event_type": "inspection",
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "study_ui_mode": normalize_study_ui_mode(study_ui_mode),
    }
    for key, value in (
        ("sequence_id", sequence_id),
        ("run_id", run_id),
        ("actor", actor),
        ("event_id", event_id),
    ):
        clean = str(value or "").strip()
        if clean:
            row[key] = clean
    return row


def build_semantic_edit_event(
    *,
    study_ui_mode: str,
    timestamp: str | None = None,
    sequence_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, str]:
    """Build a sidecar event for one clean-to-dirty semantic edit transition."""
    row = {
        "event": "semantic_edit_started",
        "event_type": "semantic_edit",
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "study_ui_mode": normalize_study_ui_mode(study_ui_mode),
    }
    for key, value in (("sequence_id", sequence_id), ("run_id", run_id)):
        clean = str(value or "").strip()
        if clean:
            row[key] = clean
    return row


def _mapping_rows(value: Iterable[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Mapping row {index} must be an object.")
        row: dict[str, str] = {}
        for field in fields:
            item = raw.get(field, "")
            if not isinstance(item, str):
                raise ValueError(f"Mapping row {index} field {field!r} must be a string.")
            row[field] = item
        rows.append(row)
    return rows


def validate_authoring_session(session: dict[str, Any]) -> None:
    if not isinstance(session, dict):
        raise ValueError("Authoring session must be an object.")
    if session.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Authoring session schema_version must be {SCHEMA_VERSION!r}.")
    if not isinstance(session.get("sequence_id"), str) or not session["sequence_id"].strip():
        raise ValueError("Authoring session sequence_id must be a non-empty string.")
    if session.get("mode") not in {"single", "dual"}:
        raise ValueError("Authoring session mode must be 'single' or 'dual'.")
    if not isinstance(session.get("audio_folder", ""), str):
        raise ValueError("Authoring session audio_folder must be a string.")
    if not isinstance(session.get("input_script", ""), str):
        raise ValueError("Authoring session input_script must be a string.")
    if not isinstance(session.get("input_context", ""), str):
        raise ValueError("Authoring session input_context must be a string.")
    characters = _mapping_rows(session.get("characters", []), ("alias", "script_name", "maya_node"))
    expected_aliases = ["A"] if session["mode"] == "single" else ["A", "B"]
    if len(characters) > 2 or [row["alias"] for row in characters] != expected_aliases[:len(characters)]:
        raise ValueError("Character mappings must be ordered aliases A, then B, with at most two rows.")
    if session["mode"] == "dual" and len(characters) not in {0, 2}:
        raise ValueError("Dual mode must contain both A and B character mappings, or no mappings.")
    _mapping_rows(session.get("look_at_targets", []), ("semantic_target", "maya_node"))
    speech_settings = session.get("jali_speech_settings")
    if speech_settings is not None:
        if not isinstance(speech_settings, dict):
            raise ValueError("jali_speech_settings must be an object when present.")
        filter_silence = speech_settings.get("filter_silence_gaps")
        threshold = speech_settings.get("silence_threshold_db")
        from_scratch = speech_settings.get("animate_from_scratch_next")
        if not isinstance(filter_silence, bool):
            raise ValueError("jali_speech_settings.filter_silence_gaps must be boolean.")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("jali_speech_settings.silence_threshold_db must be numeric.")
        if not math.isfinite(float(threshold)) or not -100.0 <= float(threshold) <= 0.0:
            raise ValueError("jali_speech_settings.silence_threshold_db must be between -100 and 0.")
        if not isinstance(from_scratch, bool):
            raise ValueError("jali_speech_settings.animate_from_scratch_next must be boolean.")
    speech_bases = session.get("jali_speech_bases", {})
    if not isinstance(speech_bases, dict):
        raise ValueError("jali_speech_bases must be an actor-keyed object.")
    required_speech_fields = (
        "script_name", "maya_node", "jsync", "sound_file", "wav_path",
        "txt_path", "txt_sha256", "preparation_status", "prepared_at",
    )
    for actor, item in speech_bases.items():
        if not isinstance(actor, str) or not actor.strip() or not isinstance(item, dict):
            raise ValueError("Each JALI speech-base entry requires a named actor and object value.")
        for field in required_speech_fields:
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"{actor}: JALI speech-base field {field!r} must be a non-empty string.")
        if item["preparation_status"] not in {"prepared", "reused"}:
            raise ValueError(f"{actor}: invalid JALI speech-base preparation_status.")
        if len(item["txt_sha256"]) != 64:
            raise ValueError(f"{actor}: txt_sha256 must be a SHA-256 hex digest.")
        item_settings = item.get("jali_settings")
        if item_settings is not None:
            if not isinstance(item_settings, dict):
                raise ValueError(f"{actor}: jali_settings must be an object.")
            if not isinstance(item_settings.get("filter_silence_gaps"), bool):
                raise ValueError(f"{actor}: invalid JALI filter_silence_gaps metadata.")
            item_threshold = item_settings.get("silence_threshold_db")
            if isinstance(item_threshold, bool) or not isinstance(item_threshold, (int, float)):
                raise ValueError(f"{actor}: invalid JALI silence_threshold_db metadata.")
            if not math.isfinite(float(item_threshold)) or not -100.0 <= float(item_threshold) <= 0.0:
                raise ValueError(f"{actor}: JALI silence_threshold_db metadata is out of range.")
        animated_from_scratch = item.get("animated_from_scratch")
        if animated_from_scratch is not None and not isinstance(animated_from_scratch, bool):
            raise ValueError(f"{actor}: animated_from_scratch metadata must be boolean.")
    inspection_events = session.get("inspection_events", [])
    if not isinstance(inspection_events, list):
        raise ValueError("Authoring session inspection_events must be a list.")
    for index, event in enumerate(inspection_events, start=1):
        if not isinstance(event, dict):
            raise ValueError(f"Inspection event {index} must be an object.")
        if event.get("event") not in INSPECTION_EVENT_NAMES:
            raise ValueError(f"Inspection event {index} has an unsupported event name.")
        if event.get("event_type", "inspection") != "inspection":
            raise ValueError(f"Inspection event {index} must have event_type 'inspection'.")
        if not isinstance(event.get("timestamp"), str) or not event["timestamp"].strip():
            raise ValueError(f"Inspection event {index} requires a timestamp.")
        normalize_study_ui_mode(event.get("study_ui_mode"))
    study_ui_sessions = session.get("study_ui_sessions", [])
    if not isinstance(study_ui_sessions, list):
        raise ValueError("Authoring session study_ui_sessions must be a list.")
    for index, ui_session in enumerate(study_ui_sessions, start=1):
        if not isinstance(ui_session, dict):
            raise ValueError(f"Study UI session {index} must be an object.")
        if not isinstance(ui_session.get("started_at"), str) or not ui_session["started_at"].strip():
            raise ValueError(f"Study UI session {index} requires started_at.")
        ended_at = ui_session.get("ended_at")
        if ended_at is not None and (not isinstance(ended_at, str) or not ended_at.strip()):
            raise ValueError(f"Study UI session {index} ended_at must be null or a timestamp.")
        normalize_study_ui_mode(ui_session.get("study_ui_mode"))
        if not isinstance(ui_session.get("initial_section_state"), dict):
            raise ValueError(f"Study UI session {index} requires initial_section_state.")
        mode_changes = ui_session.get("mode_changes", [])
        if not isinstance(mode_changes, list):
            raise ValueError(f"Study UI session {index} mode_changes must be a list.")
        for change in mode_changes:
            if not isinstance(change, dict) or not isinstance(change.get("timestamp"), str):
                raise ValueError(f"Study UI session {index} has an invalid mode change.")
            normalize_study_ui_mode(change.get("study_ui_mode"))
    semantic_edit_events = session.get("semantic_edit_events", [])
    if not isinstance(semantic_edit_events, list):
        raise ValueError("Authoring session semantic_edit_events must be a list.")
    for index, event in enumerate(semantic_edit_events, start=1):
        if not isinstance(event, dict):
            raise ValueError(f"Semantic edit event {index} must be an object.")
        if event.get("event") not in SEMANTIC_EDIT_EVENT_NAMES:
            raise ValueError(f"Semantic edit event {index} has an unsupported event name.")
        if event.get("event_type", "semantic_edit") != "semantic_edit":
            raise ValueError(
                f"Semantic edit event {index} must have event_type 'semantic_edit'."
            )
        if not isinstance(event.get("timestamp"), str) or not event["timestamp"].strip():
            raise ValueError(f"Semantic edit event {index} requires a timestamp.")
        normalize_study_ui_mode(event.get("study_ui_mode"))


def build_authoring_session(
    *,
    sequence_id: str,
    mode: str,
    audio_folder: str,
    characters: Iterable[dict[str, Any]],
    look_at_targets: Iterable[dict[str, Any]],
    input_script: str = "",
    input_context: str = "",
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build validated sidecar data while retaining unknown fields from a loaded sidecar."""
    session = deepcopy(base) if isinstance(base, dict) else {}
    session.update({
        "schema_version": SCHEMA_VERSION,
        "sequence_id": str(sequence_id).strip(),
        "mode": mode,
        "audio_folder": str(audio_folder),
        "input_script": str(input_script),
        "input_context": str(input_context),
        "characters": _mapping_rows(characters, ("alias", "script_name", "maya_node")),
        "look_at_targets": _mapping_rows(
            look_at_targets, ("semantic_target", "maya_node")
        ),
    })
    validate_authoring_session(session)
    return session


def default_authoring_session_path(
    performance_plan_path: str | Path, sequence_id: str
) -> Path:
    source = Path(performance_plan_path)
    clean_sequence = str(sequence_id).strip()
    if not clean_sequence:
        raise ValueError("sequence_id is required for an authoring-session path.")
    return source.parent / f"{clean_sequence}__authoring_session.json"


def load_authoring_session(path: str | Path) -> dict[str, Any]:
    session = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_authoring_session(session)
    return session


def save_authoring_session(session: dict[str, Any], path: str | Path) -> Path:
    validate_authoring_session(session)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output
