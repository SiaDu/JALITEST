from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from expregaze_jali.text_utils import iter_word_tokens, normalize_word


def load_words_jsonl(path: str | Path) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                words.append(json.loads(line))
    return words


def _alignment_warnings(tokens: list[dict], words: list[dict]) -> list[str]:
    warnings: list[str] = []
    if len(tokens) != len(words):
        warnings.append(f"token count mismatch: transcript={len(tokens)} textgrid={len(words)}")

    for idx, (token, word) in enumerate(zip(tokens, words)):
        word_norm = normalize_word(str(word.get("norm") or word.get("word") or ""))
        if token["norm"] != word_norm:
            warnings.append(
                f"token mismatch at {idx}: transcript={token['norm']!r} textgrid={word_norm!r}"
            )
            if len(warnings) >= 20:
                warnings.append("additional token mismatches omitted")
                break
    return warnings


def _event_token_indexes(event: dict[str, Any], tokens: list[dict]) -> list[int]:
    span = event["span"]
    start = int(span["start"])
    end = int(span["end"])
    return [
        idx
        for idx, token in enumerate(tokens)
        if token["start"] < end and token["end"] > start
    ]


def resolve_events_with_textgrid(events: dict[str, Any], words: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve event text spans to start/end seconds using TextGrid word intervals."""
    clean = events.get("clean_transcript", "")
    tokens = iter_word_tokens(clean)
    diagnostics = {
        "transcript_word_count": len(tokens),
        "textgrid_word_count": len(words),
        "alignment_warnings": _alignment_warnings(tokens, words),
        "unresolved_events": [],
    }

    resolved_events: list[dict[str, Any]] = []
    for event in events.get("events", []):
        resolved = dict(event)
        indexes = _event_token_indexes(event, tokens)
        usable = [idx for idx in indexes if idx < len(words)]
        if usable:
            first_word = words[usable[0]]
            last_word = words[usable[-1]]
            resolved["resolved_time"] = {
                "start": float(first_word["start"]),
                "end": float(last_word["end"]),
                "source": "textgrid_words",
                "start_word_index": usable[0],
                "end_word_index": usable[-1],
            }
        else:
            resolved["resolved_time"] = None
            diagnostics["unresolved_events"].append(
                {
                    "id": event.get("id"),
                    "type": event.get("type"),
                    "text": event.get("text", ""),
                    "span": event.get("span"),
                }
            )
        resolved_events.append(resolved)

    out = dict(events)
    out["events"] = resolved_events

    event_types = sorted({event.get("type") for event in resolved_events if event.get("type")})
    for event_type in event_types:
        out[event_type] = [event for event in resolved_events if event["type"] == event_type]

    for event_type in (
        "gaze",
        "mask",
        "heart",
        "lid_state",
        "performative_blink",
        "blink_suppression",
    ):
        out.setdefault(event_type, [])

    out["diagnostics"] = diagnostics
    return out
