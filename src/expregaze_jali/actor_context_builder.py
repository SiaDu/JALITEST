from __future__ import annotations

import csv
import json
import re
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
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if isinstance(value, dict):
                records.append(value)
    return records


def load_full_context_records(path: str | Path) -> list[dict[str, Any]]:
    """Load the processed full-context table.

    Supported formats:
    - CSV / TSV with a header row
    - JSONL with one shot/context record per line
    - JSON list of records, or JSON object with a common record-list key

    This replaces the old candidate_sequence dependency: context packs are now
    generated directly from full_context + shot_range.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"full_context file not found: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix == ".jsonl":
        return load_jsonl(file_path)

    if suffix == ".json":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            for key in (
                "records",
                "rows",
                "shots",
                "full_context",
                "full_context_rows",
                "full_context_local_window",
            ):
                value = data.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
        raise ValueError(f"Unsupported JSON full_context shape: {file_path}")

    delimiter = "\t" if suffix == ".tsv" else ","
    with file_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        return [dict(row) for row in reader]


def find_candidate(candidate_jsonl: str | Path, sequence_id: str) -> dict[str, Any]:
    """Deprecated compatibility helper.

    The active pipeline no longer reads candidate_sequence files. This remains so
    older tests/imports fail with a clear message instead of an ImportError.
    """
    raise RuntimeError(
        "candidate_sequence input has been removed from Step 00. "
        "Build context packs from full_context + shot_range instead."
    )


def _truncate(text: Any, max_chars: int = 1200) -> str:
    if text is None:
        return ""
    value = str(text).strip()
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[: max_chars - 20].rstrip() + " ...[truncated]"


def _safe_int(value: Any, default: int = -1) -> int:
    """Coerce common shot-index values to int.

    Handles values like 38, "38", "38.0", and shot ids such as
    "shot_0038". This is intentionally permissive because full_context files
    can come from different preprocessing stages.
    """
    try:
        if value is None or value == "":
            return default
        text = str(value).strip()
        try:
            return int(float(text))
        except Exception:
            pass
        match = re.search(r"-?\d+", text)
        if match:
            return int(match.group(0))
    except Exception:
        pass
    return default


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _get(row: dict[str, Any], *keys: str) -> Any:
    if not row:
        return ""
    normalized = {_normalize_key(str(key)): value for key, value in row.items()}
    for key in keys:
        value = normalized.get(_normalize_key(key))
        if value is not None and str(value).strip() != "":
            return value
    return ""


def _first_nonempty(rows: list[dict[str, Any]], *keys: str) -> str:
    for row in rows:
        value = _get(row, *keys)
        if str(value).strip():
            return str(value).strip()
    return ""


def _unique_join(values: list[str], sep: str = " | ") -> str:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return sep.join(out)


def _row_start_shot_idx(row: dict[str, Any]) -> int:
    value = _get(
        row,
        "shot_idx",
        "shot_index",
        "shot",
        "idx",
        "start_shot_idx",
        "shot_start_idx",
        "start_idx",
    )
    parsed = _safe_int(value)
    if parsed >= 0:
        return parsed
    return _safe_int(_get(row, "shot_id", "shot_name", "shot_identifier"))


def _row_end_shot_idx(row: dict[str, Any]) -> int:
    value = _get(row, "end_shot_idx", "shot_end_idx", "end_idx")
    parsed = _safe_int(value)
    if parsed >= 0:
        return parsed
    return _row_start_shot_idx(row)


def _row_shot_range(row: dict[str, Any]) -> tuple[int, int]:
    start = _row_start_shot_idx(row)
    end = _row_end_shot_idx(row)
    if start < 0 and end >= 0:
        start = end
    if end < 0 and start >= 0:
        end = start
    if start >= 0 and end >= 0 and end < start:
        start, end = end, start
    return start, end


def _row_shot_idx(row: dict[str, Any]) -> int:
    return _row_start_shot_idx(row)


def _row_shot_id(row: dict[str, Any]) -> str:
    shot_id = str(_get(row, "shot_id", "shot_name", "shot_identifier")).strip()
    if shot_id:
        return shot_id
    start, end = _row_shot_range(row)
    if start >= 0 and end >= 0 and start != end:
        return f"shot_{start:04d}-shot_{end:04d}"
    if start >= 0:
        return f"shot_{start:04d}"
    return ""


def _row_movie_id(row: dict[str, Any]) -> str:
    return str(_get(row, "movie_id", "imdb_id", "film_id")).strip()


def _filter_movie(rows: list[dict[str, Any]], movie_id: str | None) -> list[dict[str, Any]]:
    if not movie_id:
        return rows
    matched = [row for row in rows if _row_movie_id(row) in {"", movie_id}]
    return matched or rows


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def _sortable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sortable = [row for row in rows if _row_shot_range(row)[0] >= 0]
    sortable.sort(key=lambda row: (_row_shot_range(row)[0], _row_shot_range(row)[1], _row_shot_id(row)))
    return sortable


def _filter_shot_window(
    rows: list[dict[str, Any]],
    *,
    start_shot_idx: int,
    end_shot_idx: int,
    window: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target_lo = min(start_shot_idx, end_shot_idx)
    target_hi = max(start_shot_idx, end_shot_idx)
    local_lo = target_lo - max(window, 0)
    local_hi = target_hi + max(window, 0)

    sortable = _sortable_rows(rows)
    target_rows: list[dict[str, Any]] = []
    local_rows: list[dict[str, Any]] = []
    for row in sortable:
        row_start, row_end = _row_shot_range(row)
        if _ranges_overlap(row_start, row_end, target_lo, target_hi):
            target_rows.append(row)
        if _ranges_overlap(row_start, row_end, local_lo, local_hi):
            local_rows.append(row)
    return target_rows, local_rows


def _row_script_text(row: dict[str, Any]) -> str:
    explicit = str(
        _get(
            row,
            "aligned_script_text",
            "script_text",
            "script_action_text",
            "current_script_text",
            "script",
            "action_text",
            "scene_script_text",
        )
    ).strip()
    if explicit:
        return explicit

    pieces = [
        str(_get(row, "prev_other_text", "prev_action_text", "previous_action_text")).strip(),
        str(_get(row, "bridge_other_text", "bridge_action_text")).strip(),
        str(_get(row, "aligned_script_dialogue", "dialogue", "dialogue_text", "script_dialogue")).strip(),
        str(_get(row, "next_other_text", "next_action_text")).strip(),
    ]
    return " -- ".join(piece for piece in pieces if piece)


def _join_row_text(rows: list[dict[str, Any]], *keys: str, sep: str = "\n") -> str:
    values = []
    for row in rows:
        value = str(_get(row, *keys)).strip()
        if value:
            values.append(value)
    return sep.join(values).strip()


def _local_window_payload(rows: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append(
            {
                "shot_id": _row_shot_id(row),
                "shot_idx": _row_shot_idx(row),
                "aligned_script_text": _truncate(_row_script_text(row), max_chars),
            }
        )
    return payload


def _infer_sequence_id(movie_id: str | None, start_shot_idx: int, end_shot_idx: int) -> str:
    prefix = movie_id or "sequence"
    return f"{prefix}:shot_{start_shot_idx:04d}-shot_{end_shot_idx:04d}"


def _default_exact_transcript(target_rows: list[dict[str, Any]]) -> str:
    explicit = _join_row_text(target_rows, "exact_transcript", "transcript", "speech_text", "line_text", sep="\n")
    if explicit:
        return explicit
    subtitle = _join_row_text(target_rows, "subtitle_text", "subtitle", sep="\n")
    if subtitle:
        return subtitle
    dialogue = _join_row_text(target_rows, "aligned_script_dialogue", "dialogue", "dialogue_text", "script_dialogue", sep="\n")
    if dialogue:
        return dialogue
    return _unique_join([_row_script_text(row) for row in target_rows], sep="\n")


def _diagnose_shot_matching(rows: list[dict[str, Any]], *, start_shot_idx: int, end_shot_idx: int, max_examples: int = 8) -> str:
    fieldnames: list[str] = []
    seen_fields: set[str] = set()
    for row in rows[:50]:
        for key in row.keys():
            if key not in seen_fields:
                seen_fields.add(key)
                fieldnames.append(str(key))

    parsed_ranges: list[tuple[int, int, str]] = []
    unparsed_examples: list[str] = []
    for row in rows:
        start, end = _row_shot_range(row)
        if start >= 0:
            parsed_ranges.append((start, end, _row_shot_id(row)))
        elif len(unparsed_examples) < max_examples:
            compact = {key: row.get(key) for key in list(row.keys())[:8]}
            unparsed_examples.append(json.dumps(compact, ensure_ascii=False))

    lines = [
        f"Rows loaded: {len(rows)}",
        f"Requested shot_range: {start_shot_idx}-{end_shot_idx}",
        f"Columns: {fieldnames}",
    ]
    if parsed_ranges:
        starts = [item[0] for item in parsed_ranges]
        ends = [item[1] for item in parsed_ranges]
        examples = [f"{start}-{end}:{shot_id}" for start, end, shot_id in parsed_ranges[:max_examples]]
        lines.extend(
            [
                f"Parsed shot range coverage: {min(starts)}-{max(ends)}",
                f"Parsed shot examples: {examples}",
            ]
        )
    else:
        lines.append(
            "No parseable shot index found. Expected one of: shot_idx, shot_index, "
            "shot, idx, start_shot_idx/end_shot_idx, or shot_id like shot_0038."
        )
    if unparsed_examples:
        lines.append(f"Unparsed row examples: {unparsed_examples}")
    return "\n".join(lines)


def build_context_pack_from_shot_range(
    full_context_rows: list[dict[str, Any]],
    *,
    start_shot_idx: int,
    end_shot_idx: int,
    movie_id: str | None = None,
    movie_name: str | None = None,
    sequence_id: str | None = None,
    local_window: int = 3,
    exact_transcript: str | None = None,
    max_story_chars: int = 12000,
    max_script_chars: int = 5000,
    max_window_script_chars: int = 2500,
) -> dict[str, Any]:
    """Build a context pack directly from full_context + shot_range.

    The output follows the lightweight `_context_pack_templete.json` shape and is
    generated by json.dumps, so punctuation, quotes, and newlines are escaped
    safely. Users should edit the shot_range in YAML instead of hand-writing JSON.
    """
    rows = _filter_movie(full_context_rows, movie_id)
    target_rows, local_rows = _filter_shot_window(
        rows,
        start_shot_idx=start_shot_idx,
        end_shot_idx=end_shot_idx,
        window=local_window,
    )
    if not target_rows:
        diagnostics = _diagnose_shot_matching(
            rows,
            start_shot_idx=start_shot_idx,
            end_shot_idx=end_shot_idx,
        )
        raise ValueError(
            "No full_context rows matched shot_range "
            f"{start_shot_idx}-{end_shot_idx}. Check configs/path_local.yaml or the full_context schema.\n"
            f"{diagnostics}"
        )

    inferred_movie_id = movie_id or _row_movie_id(target_rows[0]) or _row_movie_id(rows[0]) if rows else None
    resolved_movie_name = (
        movie_name
        or _first_nonempty(rows, "movie_name", "movie_title", "title", "film_title")
        or inferred_movie_id
        or ""
    )
    resolved_sequence_id = sequence_id or _infer_sequence_id(inferred_movie_id, start_shot_idx, end_shot_idx)

    storyline = _first_nonempty(
        rows,
        "storyline",
        "movie_storyline",
        "plot_summary",
        "overview",
        "movie_overview",
    )
    current_story_description = _first_nonempty(
        target_rows + local_rows,
        "current_story_description",
        "story_description",
        "local_story_description",
        "scene_description",
    )

    full_story_description = _first_nonempty(
        rows,
        "full_story_description",
        "full_story",
        "full_plot_summary",
    )
    if not full_story_description:
        full_story_description = _unique_join(
            [str(_get(row, "story_description", "current_story_description")).strip() for row in rows],
            sep=" | ",
        )

    current_script_text = _unique_join([_row_script_text(row) for row in local_rows], sep=" | ")
    subtitle_text = _join_row_text(target_rows, "subtitle_text", "subtitle", sep="\n")
    aligned_script_dialogue = _join_row_text(target_rows, "aligned_script_dialogue", "dialogue", "dialogue_text", sep="\n")
    resolved_exact_transcript = (exact_transcript if exact_transcript is not None else _default_exact_transcript(target_rows)).strip()

    return {
        "movie_name": resolved_movie_name,
        "movie_id": inferred_movie_id,
        "sequence_id": resolved_sequence_id,
        "shot_range": {
            "start_shot_idx": start_shot_idx,
            "end_shot_idx": end_shot_idx,
            "shot_count": len(target_rows),
        },
        "storyline": _truncate(storyline, max_story_chars),
        "current_story_description": _truncate(current_story_description, max_story_chars),
        "current_script_text": _truncate(current_script_text, max_script_chars),
        "subtitle_text": subtitle_text,
        "aligned_script_dialogue": aligned_script_dialogue,
        "exact_transcript": resolved_exact_transcript,
        "full_story_description": _truncate(full_story_description, max_story_chars),
        "full_context_local_window": _local_window_payload(local_rows, max_window_script_chars),
    }


# Backward-compatible alias for old imports. The old implementation used a
# candidate record; the active Step 00 no longer calls this function.
def build_actor_context_pack(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise RuntimeError(
        "build_actor_context_pack(candidate, ...) has been replaced by "
        "build_context_pack_from_shot_range(full_context_rows, shot_range=...)."
    )


# Backward-compatible alias. Prefer build_context_pack_from_shot_range.
def load_full_context_window(
    full_context_csv: str | Path,
    movie_id: str,
    start_shot_idx: int,
    end_shot_idx: int,
    window: int = 2,
) -> list[dict[str, Any]]:
    rows = load_full_context_records(full_context_csv)
    rows = _filter_movie(rows, movie_id)
    _, local_rows = _filter_shot_window(
        rows,
        start_shot_idx=start_shot_idx,
        end_shot_idx=end_shot_idx,
        window=window,
    )
    return local_rows
