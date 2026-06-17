from __future__ import annotations

from typing import Any

from expregaze_jali.text_utils import iter_word_tokens

STATE_CHANGE_TYPES = (
    "gaze",
    "mask",
    "heart",
    "lid_state",
    "blink_suppression",
)

ANCHOR_TYPES = (
    "performative_blink",
)


def _trim_span(text: str, start: int, end: int) -> tuple[int, int, str]:
    start = max(0, min(start, len(text)))
    end = max(0, min(end, len(text)))
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end, text[start:end]


def _anchor_span(text: str, position: int, max_words: int = 3) -> tuple[int, int, str]:
    """
    Resolve an anchor tag such as <pb01=...> to a short local text span.

    Unlike gaze/mask/heart/lid_state, performative blinks are not persistent
    state-change events unless the LLM provides an explicit closing tag.
    """
    tokens = iter_word_tokens(text)
    if not tokens:
        return 0, 0, ""

    token_idx = None
    for idx, token in enumerate(tokens):
        if token["end"] > position:
            token_idx = idx
            break

    if token_idx is None:
        token_idx = len(tokens) - 1

    end_idx = min(len(tokens) - 1, token_idx + max(1, max_words) - 1)
    start = int(tokens[token_idx]["start"])
    end = int(tokens[end_idx]["end"])
    return _trim_span(text, start, end)


def _make_event(tag: dict[str, Any], tag_type: str, start: int, end: int, text: str) -> dict[str, Any]:
    return {
        "id": tag["id"],
        "type": tag_type,
        "value": tag["value"],
        "text": text,
        "reason": tag.get("reason", ""),
        "span": {
            "start": start,
            "end": end,
            "raw_start": int(tag["position"]),
            "raw_end": end,
        },
        "order": tag["order"],
    }


def _state_end(clean_len: int, typed_tags: list[dict[str, Any]], idx: int) -> int:
    tag = typed_tags[idx]
    explicit_end = tag.get("explicit_end")
    if explicit_end is not None and int(explicit_end) >= int(tag["position"]):
        return int(explicit_end)
    return int(typed_tags[idx + 1]["position"]) if idx + 1 < len(typed_tags) else clean_len


def compile_state_change_events(parsed: dict[str, Any]) -> dict[str, Any]:
    """
    Convert readable performance tags into structured text-span events.

    State-change tags:
        gaze / mask / heart / lid_state / blink_suppression
        end at an explicit closing tag if present; otherwise at the next tag of
        the same type, or the end of the transcript.

    Anchor tags:
        performative_blink
        use an explicit closing tag if present; otherwise resolve to a short
        nearby phrase and do not persist until next pb tag.
    """
    clean = parsed.get("clean_transcript", "")
    tags = sorted(parsed.get("tags", []), key=lambda tag: (tag["position"], tag["order"]))
    events: list[dict[str, Any]] = []

    for tag_type in STATE_CHANGE_TYPES:
        typed_tags = [tag for tag in tags if tag.get("type") == tag_type]
        for idx, tag in enumerate(typed_tags):
            raw_start = int(tag["position"])
            raw_end = _state_end(len(clean), typed_tags, idx)
            start, end, span_text = _trim_span(clean, raw_start, raw_end)
            events.append(_make_event(tag, tag_type, start, end, span_text))

    for tag_type in ANCHOR_TYPES:
        typed_tags = [tag for tag in tags if tag.get("type") == tag_type]
        for tag in typed_tags:
            explicit_end = tag.get("explicit_end")
            if explicit_end is not None and int(explicit_end) > int(tag["position"]):
                start, end, span_text = _trim_span(clean, int(tag["position"]), int(explicit_end))
            else:
                start, end, span_text = _anchor_span(clean, int(tag["position"]), max_words=3)
            events.append(_make_event(tag, tag_type, start, end, span_text))

    events = sorted(events, key=lambda event: (event["span"]["start"], event["order"], event["type"]))

    event_types = sorted({event["type"] for event in events})
    out = {
        "clean_transcript": clean,
        "events": events,
        "diagnostics": {},
    }
    for event_type in event_types:
        out[event_type] = [event for event in events if event["type"] == event_type]

    # Preserve old keys even when absent.
    for event_type in (
        "gaze",
        "mask",
        "heart",
        "lid_state",
        "performative_blink",
        "blink_suppression",
    ):
        out.setdefault(event_type, [])

    return out
