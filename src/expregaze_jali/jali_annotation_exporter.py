from __future__ import annotations

from collections import defaultdict
import re
from typing import Any
from expregaze_jali.text_utils import iter_word_tokens, normalize_word, match_anchor_word_sequence


def _jali_tag_name(event_type: str) -> str:
    if event_type not in {"mask", "heart"}:
        raise ValueError(f"Unsupported JALI event type: {event_type}")
    return event_type


def _event_value(event: dict[str, Any]) -> str:
    value = str(event["value"])
    if event["type"] == "heart" and "-" in value:
        source, strength = value.rsplit("-", 1)
        if source and strength:
            return f"{source}-{strength}"
    return value


def _open_tag(event: dict[str, Any]) -> str:
    name = _jali_tag_name(event["type"])
    return f"<{name}={_event_value(event)}>"


def _close_tag(event: dict[str, Any]) -> str:
    name = _jali_tag_name(event["type"])
    return f"</{name}={_event_value(event)}>"

_JALI_TAG_RE = re.compile(r"(</?(?:mask|heart)=[^>]+>)")


def _space_jali_tags(text: str) -> str:
    # Maya/JALI Text Editor is not a tolerant XML parser here. In practice it can
    # misread `<mask=...>Word` or `</mask=...><mask=...>` as malformed text and
    # report a closing tag without an opening tag. Keep every mask/heart tag as a
    # whitespace-delimited standalone token.
    text = re.sub(r"(?<!\s)(</?(?:mask|heart)=[^>]+>)", r" \1", text)
    text = re.sub(r"(</?(?:mask|heart)=[^>]+>)(?!\s)", r"\1 ", text)
    return text.strip()

def export_jali_annotation(parsed: dict[str, Any], events: dict[str, Any]) -> str:
    """
    Export a JALI-compatible transcript annotation.

    Gaze / lid / blink tags are omitted. Mask and heart state-change events are
    converted to JALI transcript tags while preserving values.

    Important JALI Text Editor convention:
    when multiple JALI tags close at the same transcript position, close them in
    the same order they opened, not XML stack order. For example, if the span
    begins as:

        <mask=Friendly-70><heart=Happy-30>text

    the JALI-facing transcript should end that shared span as:

        text</mask=Friendly-70></heart=Happy-30>

    This intentionally differs from well-formed XML (`</heart></mask>`), but it
    matches the ordering expected by the JALI tag workflow used in this project.
    """
    clean = parsed.get("clean_transcript", "")
    opens: dict[int, list[dict[str, Any]]] = defaultdict(list)
    closes: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for event in events.get("events", []):
        if event.get("type") not in {"mask", "heart"}:
            continue
        start = int(event["span"]["start"])
        end = int(event["span"]["end"])
        if end <= start:
            continue
        opens[start].append(event)
        closes[end].append(event)

    parts: list[str] = []
    for pos in range(len(clean) + 1):
        if pos in closes:
            # JALI tag order: close in opening/order order, not reverse stack order.
            for event in sorted(closes[pos], key=lambda item: item["order"]):
                parts.append(_close_tag(event))
        if pos in opens:
            for event in sorted(opens[pos], key=lambda item: item["order"]):
                parts.append(_open_tag(event))
        if pos < len(clean):
            parts.append(clean[pos])

    return _space_jali_tags("".join(parts))


def build_dual_speaker_jali_annotation(source_text: str, phrases: list[dict[str, Any]], *, alias: str, script_name: str, mask_only: bool = False) -> tuple[str, dict[str, Any]]:
    """Tag only this actor's spoken phrases, preserving its original transcript."""
    tokens = iter_word_tokens(source_text); cursor = 0; events: list[dict[str, Any]] = []
    included: list[str] = []; skipped: list[str] = []; order = 0
    for phrase in phrases:
        phrase_id = str(phrase.get("phrase_id", "?"))
        if str(phrase.get("speaker")) != alias:
            skipped.append(phrase_id); continue
        wanted = [item["norm"] for item in iter_word_tokens(str((phrase.get("span") or {}).get("text", "")))]
        if not wanted: raise ValueError(f"{alias} {phrase_id}: speaker phrase has no words to map.")
        actual = [item["norm"] for item in tokens[cursor:cursor + len(wanted)]]
        if actual != wanted:
            raise ValueError(f"{alias} {phrase_id}: isolated transcript words do not match phrase; expected {wanted}, found {actual}.")
        start, end = tokens[cursor]["start"], tokens[cursor + len(wanted) - 1]["end"]
        cursor += len(wanted); included.append(phrase_id)
        state = ((phrase.get("states") or {}).get(alias) or {})
        channels = (("affect", "mask"),) if mask_only else (("affect", "mask"), ("heart", "heart"))
        for channel, kind in channels:
            value = state.get(channel)
            if value not in (None, "", "NONE"):
                events.append({"type": kind, "value": str(value), "span": {"start": start, "end": end}, "order": order, "phrase_id": phrase_id}); order += 1
    if cursor != len(tokens):
        remaining = [item["text"] for item in tokens[cursor:cursor + 5]]
        raise ValueError(f"{alias}: isolated transcript has unconsumed word(s): {remaining}.")
    text = export_jali_annotation({"clean_transcript": source_text}, {"events": events})
    diagnostic = {"actor": alias, "script_name": script_name, "included_phrase_ids": included, "skipped_listener_phrase_ids": skipped, "mask_tag_count": sum(e["type"] == "mask" for e in events), "events": events}
    if not mask_only:
        diagnostic["heart_tag_count"] = sum(e["type"] == "heart" for e in events)
    return text, diagnostic


def build_sparse_speaker_jali_annotation(
    source_text: str, spoken_anchors: list[dict[str, Any]], *, actor: str, script_name: str
) -> tuple[str, dict[str, Any]]:
    """Annotate persistent affect across this actor's isolated spoken words."""
    tokens = iter_word_tokens(source_text)
    token_texts = [str(token["text"]) for token in tokens]
    cursor = 0
    anchor_token_spans: list[tuple[int, int]] = []
    for anchor in spoken_anchors:
        consumed = match_anchor_word_sequence(str(anchor["text"]), token_texts, cursor)
        if consumed is None:
            raise ValueError(
                f"{actor}: isolated transcript could not align anchor {anchor['anchor_id']} "
                f"{anchor['text']!r}; next tokens: {token_texts[cursor:cursor + 3]!r}."
            )
        anchor_token_spans.append((cursor, cursor + consumed))
        cursor += consumed
    if cursor != len(tokens):
        remaining = token_texts[cursor:cursor + 5]
        raise ValueError(f"{actor}: isolated transcript has unconsumed word(s): {remaining}.")
    events: list[dict[str, Any]] = []
    segment_start = 0
    active: str | None = None
    order = 0
    for index, anchor in enumerate(spoken_anchors + [{"affect": None}]):
        value = anchor.get("affect")
        normalized = None if value in (None, "NONE", "MASK-NONE") else str(value)
        if index == 0:
            active = normalized
            continue
        same_turn = index < len(spoken_anchors) and anchor.get("turn_id") == spoken_anchors[index - 1].get("turn_id")
        if normalized == active and same_turn:
            continue
        if active is not None:
            events.append({
                "type": "mask", "value": active,
                "span": {"start": tokens[anchor_token_spans[segment_start][0]]["start"], "end": tokens[anchor_token_spans[index - 1][1] - 1]["end"]},
                "order": order, "anchor_id": spoken_anchors[segment_start]["anchor_id"],
            })
            order += 1
        segment_start = index
        active = normalized
    text = export_jali_annotation({"clean_transcript": source_text}, {"events": events})
    return text, {
        "actor": actor, "script_name": script_name, "mask_tag_count": len(events),
        "spoken_anchor_ids": [row["anchor_id"] for row in spoken_anchors],
        "anchor_token_spans": anchor_token_spans, "events": events,
    }
