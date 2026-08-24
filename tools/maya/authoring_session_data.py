"""Pure-Python persistence for Maya performance-authoring session mappings."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "authoring_session_v0"


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
    characters = _mapping_rows(session.get("characters", []), ("alias", "script_name", "maya_node"))
    expected_aliases = ["A"] if session["mode"] == "single" else ["A", "B"]
    if len(characters) > 2 or [row["alias"] for row in characters] != expected_aliases[:len(characters)]:
        raise ValueError("Character mappings must be ordered aliases A, then B, with at most two rows.")
    if session["mode"] == "dual" and len(characters) not in {0, 2}:
        raise ValueError("Dual mode must contain both A and B character mappings, or no mappings.")
    _mapping_rows(session.get("look_at_targets", []), ("semantic_target", "maya_node"))


def build_authoring_session(
    *,
    sequence_id: str,
    mode: str,
    audio_folder: str,
    characters: Iterable[dict[str, Any]],
    look_at_targets: Iterable[dict[str, Any]],
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build validated sidecar data while retaining unknown fields from a loaded sidecar."""
    session = deepcopy(base) if isinstance(base, dict) else {}
    session.update({
        "schema_version": SCHEMA_VERSION,
        "sequence_id": str(sequence_id).strip(),
        "mode": mode,
        "audio_folder": str(audio_folder),
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
