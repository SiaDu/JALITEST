from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Load newline-delimited JSON records."""
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return records


def find_candidate(candidate_jsonl: str | Path, sequence_id: str) -> dict[str, Any]:
    """Return one candidate sequence by sequence_id."""
    for record in load_jsonl(candidate_jsonl):
        if record.get("sequence_id") == sequence_id:
            return record
    raise ValueError(f"sequence_id not found: {sequence_id}")


def _truncate(text: Any, max_chars: int = 1200) -> str:
    if text is None:
        return ""
    value = str(text).strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 20].rstrip() + " ...[truncated]"


def _safe_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_shots(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    shots = candidate.get("shots") or []
    if isinstance(shots, str):
        try:
            parsed = json.loads(shots)
            if isinstance(parsed, list):
                return [shot for shot in parsed if isinstance(shot, dict)]
        except Exception:
            return []
    if isinstance(shots, list):
        return [shot for shot in shots if isinstance(shot, dict)]
    return []


def load_full_context_window(
    full_context_csv: str | Path,
    movie_id: str,
    start_shot_idx: int,
    end_shot_idx: int,
    window: int = 2,
) -> list[dict[str, Any]]:
    """
    Load only a local shot window from the processed MovieNet full-context CSV.

    This avoids sending the entire full-context file to the LLM while still giving
    it enough story and staging information for actor-style annotation.
    """
    path = Path(full_context_csv)
    if not path.exists() or path.stat().st_size == 0:
        return []

    rows: list[dict[str, Any]] = []
    lo = start_shot_idx - window
    hi = end_shot_idx + window

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("movie_id") != movie_id:
                continue
            shot_idx = _safe_int(row.get("shot_idx"))
            if lo <= shot_idx <= hi:
                rows.append(row)

    return rows


def _candidate_blob(candidate: dict[str, Any], shots: list[dict[str, Any]]) -> str:
    parts = [
        str(candidate.get("prototype_label", "")),
        str(candidate.get("script_action_preview", "")),
    ]
    for shot in shots:
        parts.extend(
            [
                str(shot.get("subtitle_text", "")),
                str(shot.get("aligned_script_dialogue", "")),
                str(shot.get("prev_other_text", "")),
                str(shot.get("bridge_other_text", "")),
                str(shot.get("next_other_text", "")),
            ]
        )
    return "\n".join(parts)


def infer_scene_targets(candidate: dict[str, Any], shots: list[dict[str, Any]]) -> dict[str, list[str]]:
    """
    Infer a compact target hint list for the LLM prompt.

    The result is only a hint, not a closed vocabulary. The LLM may still use a
    concrete target that the keyword sweep missed, and the Maya stage can resolve
    it with a manual target map later.
    """
    text = _candidate_blob(candidate, shots).lower()

    objects: list[str] = []
    keyword_targets = {
        "crystal": "CRYSTAL",
        "photograph": "PHOTOGRAPH",
        "photo": "PHOTOGRAPH",
        "picture": "PHOTOGRAPH",
        "balloon": "BALLOON",
        "medal": "MEDAL",
        "door": "DOOR",
        "throne": "THRONE",
        "bag": "BAG",
        "window": "WINDOW",
        "wagon": "WAGON",
        "chair": "CHAIR",
        "table": "TABLE",
        "book": "BOOK",
        "letter": "LETTER",
    }
    for keyword, target in keyword_targets.items():
        if keyword in text and target not in objects:
            objects.append(target)

    people: list[str] = []
    for name in (
        "DOROTHY",
        "PROFESSOR",
        "WIZARD",
        "SCARECROW",
        "TIN_MAN",
        "LION",
        "AUNT_EM",
        "UNCLE_HENRY",
        "TOTO",
    ):
        needle = name.lower().replace("_", " ")
        if needle in text and name not in people:
            people.append(name)

    active_speakers = candidate.get("active_speakers") or []
    for speaker in active_speakers:
        if isinstance(speaker, str):
            normalized = speaker.strip().upper().replace(" ", "_")
            if normalized and normalized not in people:
                people.append(normalized)

    directions = [
        "LISTENER",
        "DOWN",
        "DOWN_LEFT",
        "DOWN_RIGHT",
        "UP",
        "UP_LEFT",
        "UP_RIGHT",
        "LEFT",
        "RIGHT",
    ]

    return {
        "people": people,
        "objects": objects,
        "directions": directions,
    }


def _extract_exact_transcript(shots: list[dict[str, Any]]) -> str:
    """
    Default exact transcript for annotation.

    This is intentionally simple: use candidate shot subtitle_text. Users may
    override or manually edit exact_transcript before running the LLM.
    """
    return "\n".join(
        str(shot.get("subtitle_text", "")).strip()
        for shot in shots
        if str(shot.get("subtitle_text", "")).strip()
    ).strip()


def _join_shot_field(shots: list[dict[str, Any]], field: str) -> str:
    return "\n".join(
        str(shot.get(field, "")).strip()
        for shot in shots
        if str(shot.get(field, "")).strip()
    ).strip()


def _first_story_description(full_context_rows: list[dict[str, Any]]) -> str:
    for row in full_context_rows:
        value = row.get("story_description")
        if value:
            return str(value)
    return ""


def build_target_context(candidate: dict[str, Any], scene_targets: dict[str, list[str]]) -> dict[str, Any]:
    """Build semantic-to-concrete target hints for the LLM and exporter."""
    active_speakers = [
        str(s).strip().upper().replace(" ", "_")
        for s in (candidate.get("active_speakers") or [])
        if str(s).strip()
    ]
    speaking_character = active_speakers[0] if active_speakers else None

    people = list(scene_targets.get("people", []))
    objects = list(scene_targets.get("objects", []))

    listener_candidates = [p for p in people if p != speaking_character]
    primary_listener = listener_candidates[0] if listener_candidates else None

    primary_object = None
    if "CRYSTAL" in objects:
        primary_object = "CRYSTAL"
    elif len(objects) == 1:
        primary_object = objects[0]

    role_map: dict[str, str] = {}
    if speaking_character:
        role_map["SPEAKER"] = speaking_character
    if primary_listener:
        role_map["LISTENER"] = primary_listener
    if primary_object:
        role_map["PRIMARY_OBJECT"] = primary_object

    notes: list[str] = []
    if primary_listener:
        notes.append(f"LISTENER most likely refers to {primary_listener}.")
    if objects:
        notes.append(
            "Object targets are hints only; prefer specific targets when inferable, "
            "but generic OBJECT may be used when intentionally unresolved."
        )

    return {
        "speaking_character": speaking_character,
        "primary_listener": primary_listener,
        "listener_candidates": listener_candidates,
        "object_candidates": objects,
        "direction_targets": scene_targets.get("directions", []),
        "role_map": role_map,
        "notes": notes,
    }


def build_actor_context_pack(
    candidate: dict[str, Any],
    full_context_rows: list[dict[str, Any]] | None = None,
    *,
    exact_transcript: str | None = None,
    max_story_chars: int = 900,
    max_action_chars: int = 900,
    max_dialogue_chars: int = 1200,
) -> dict[str, Any]:
    """Build the compact context object that will be injected into the LLM prompt."""
    full_context_rows = full_context_rows or []
    shots = _as_shots(candidate)
    default_exact_transcript = _extract_exact_transcript(shots)
    scene_targets = infer_scene_targets(candidate, shots)
    target_context = build_target_context(candidate, scene_targets)

    subtitle_text = _join_shot_field(shots, "subtitle_text")
    aligned_script_dialogue = _join_shot_field(shots, "aligned_script_dialogue")

    candidate_shots: list[dict[str, Any]] = []
    for shot in shots:
        candidate_shots.append(
            {
                "shot_idx": shot.get("shot_idx"),
                "shot_id": shot.get("shot_id"),
                "time": {
                    "start": shot.get("shot_start_time_hms"),
                    "end": shot.get("shot_end_time_hms"),
                },
                "subtitle_text": _truncate(shot.get("subtitle_text"), max_dialogue_chars),
                "aligned_script_dialogue": _truncate(shot.get("aligned_script_dialogue"), max_dialogue_chars),
                "aligned_speakers": shot.get("aligned_speakers"),
                "prev_other_text": _truncate(shot.get("prev_other_text"), max_action_chars),
                "bridge_other_text": _truncate(shot.get("bridge_other_text"), max_action_chars),
                "next_other_text": _truncate(shot.get("next_other_text"), max_action_chars),
                "match_score": shot.get("match_score"),
            }
        )

    full_window: list[dict[str, Any]] = []
    for row in full_context_rows:
        full_window.append(
            {
                "shot_idx": row.get("shot_idx"),
                "shot_id": row.get("shot_id"),
                "time": {
                    "start": row.get("shot_start_time_hms"),
                    "end": row.get("shot_end_time_hms"),
                },
                "subtitle_text": _truncate(row.get("subtitle_text"), max_dialogue_chars),
                "aligned_script_dialogue": _truncate(row.get("aligned_script_dialogue"), max_dialogue_chars),
                "aligned_speakers": row.get("aligned_speakers"),
                "prev_other_text": _truncate(row.get("prev_other_text"), max_action_chars),
                "next_other_text": _truncate(row.get("next_other_text"), max_action_chars),
                "match_score": row.get("match_score"),
            }
        )

    return {
        "movie_id": candidate.get("movie_id"),
        "sequence_id": candidate.get("sequence_id"),
        "prototype_label": candidate.get("prototype_label"),
        "time": {
            "start": candidate.get("start_time_hms"),
            "end": candidate.get("end_time_hms"),
            "duration_sec": candidate.get("total_sec"),
        },
        "shot_range": {
            "start_shot_idx": candidate.get("start_shot_idx"),
            "end_shot_idx": candidate.get("end_shot_idx"),
            "shot_count": candidate.get("shot_count"),
        },
        "active_speakers": candidate.get("active_speakers"),
        "story_card": _truncate(_first_story_description(full_context_rows), max_story_chars),
        "script_action_preview": _truncate(candidate.get("script_action_preview"), 1800),
        "subtitle_text": subtitle_text,
        "aligned_script_dialogue": aligned_script_dialogue,
        "scene_targets": scene_targets,
        "target_context": target_context,
        "exact_transcript": (exact_transcript if exact_transcript is not None else default_exact_transcript).strip(),
        "candidate_shots": candidate_shots,
        "full_context_local_window": full_window,
    }
