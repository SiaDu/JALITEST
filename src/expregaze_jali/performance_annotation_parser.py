from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SECTION_PATTERN = re.compile(r"^\[(ANALYZE|ANNOTATION|REASONS)\]\s*$", re.MULTILINE)

# Supported readable annotation ids:
#   g01  = gaze
#   m01  = visible mask / surface expression
#   h01  = hidden heart / internal undercurrent
#   l01  = lid_state
#   pb01 = performative blink
#   bs01 = blink suppression
TAG_ID_PATTERN = r"(?:pb|bs|[gmhl])\d+"
ANY_TAG_PATTERN = re.compile(rf"</({TAG_ID_PATTERN})>|<({TAG_ID_PATTERN})=([^<>]+)>")
# Compatibility only: recover accidental naked tags such as `g01=GAZE-CHARACTER_DOROTHY`.
# Prompt rules now forbid this, but old LLM outputs can still be compiled safely.
BARE_TAG_PATTERN = re.compile(rf"(?<![<\w/])({TAG_ID_PATTERN})=([^\s<>]+)")
REASON_COLON_PATTERN = re.compile(rf"^\s*({TAG_ID_PATTERN})(?:\s*=\s*[^:]+)?\s*:\s*(.*?)\s*$")
REASON_HEADER_PATTERN = re.compile(rf"^\s*({TAG_ID_PATTERN})(?:\s*=\s*(.*?))?\s*$")

TAG_TYPES = {
    "g": "gaze",
    "m": "mask",
    "h": "heart",
    "l": "lid_state",
    "pb": "performative_blink",
    "bs": "blink_suppression",
}

PREFIX_PATTERN = re.compile(r"^(pb|bs|[gmhl])(\d+)$")


def _read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _parse_sections(text: str) -> tuple[dict[str, str], list[str]]:
    matches = list(SECTION_PATTERN.finditer(text))
    sections: dict[str, str] = {}
    warnings: list[str] = []

    for idx, match in enumerate(matches):
        name = match.group(1)
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections[name] = text[body_start:body_end].strip()

    for required in ("ANALYZE", "ANNOTATION", "REASONS"):
        if required not in sections:
            warnings.append(f"missing section: {required}")

    return sections, warnings


def _tag_prefix(tag_id: str) -> str:
    match = PREFIX_PATTERN.match(tag_id)
    if not match:
        raise ValueError(f"Unsupported tag id: {tag_id!r}")
    return match.group(1)


def _tag_type(tag_id: str) -> str:
    prefix = _tag_prefix(tag_id)
    return TAG_TYPES[prefix]


def _normalize_bare_tags(annotation_text: str) -> tuple[str, list[dict[str, Any]]]:
    """Convert accidental naked tags into opening tags.

    Desired syntax is XML-like: `<g01=GAZE-CHARACTER_DOROTHY>text</g01>`.
    Older/bad LLM output may produce `g01=GAZE-CHARACTER_DOROTHY text`.
    We recover by converting it to `<g01=GAZE-CHARACTER_DOROTHY> text`.
    """
    normalized: list[dict[str, Any]] = []

    def repl(match: re.Match[str]) -> str:
        tag_id = match.group(1)
        value = match.group(2)
        normalized.append(
            {
                "id": tag_id,
                "value": value,
                "text": match.group(0),
                "raw_start": match.start(),
                "raw_end": match.end(),
            }
        )
        return f"<{tag_id}={value}>"

    return BARE_TAG_PATTERN.sub(repl, annotation_text), normalized


def _strip_tags_and_collect(annotation_text: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove readable tags from transcript and collect opening/closing metadata.

    Closing tags such as </m01> or </g03> are stripped from the clean transcript and
    used as explicit span ends for matching opening tags.
    """
    annotation_text, normalized_bare_tags = _normalize_bare_tags(annotation_text)

    clean_parts: list[str] = []
    tags: list[dict[str, Any]] = []
    stripped_closing_tags: list[dict[str, Any]] = []
    clean_pos = 0
    raw_pos = 0

    for match in ANY_TAG_PATTERN.finditer(annotation_text):
        before = annotation_text[raw_pos : match.start()]
        clean_parts.append(before)
        clean_pos += len(before)

        if match.group(1):  # closing tag
            tag_id = match.group(1)
            stripped_closing_tags.append(
                {
                    "id": tag_id,
                    "text": match.group(0),
                    "raw_start": match.start(),
                    "raw_end": match.end(),
                    "position": clean_pos,
                }
            )
        else:  # opening tag
            tag_id = match.group(2)
            tags.append(
                {
                    "id": tag_id,
                    "prefix": _tag_prefix(tag_id),
                    "type": _tag_type(tag_id),
                    "value": match.group(3).strip(),
                    "position": clean_pos,
                    "raw_start": match.start(),
                    "raw_end": match.end(),
                    "order": len(tags),
                }
            )

        raw_pos = match.end()

    tail = annotation_text[raw_pos:]
    clean_parts.append(tail)

    closing_by_id: dict[str, list[dict[str, Any]]] = {}
    for close in stripped_closing_tags:
        closing_by_id.setdefault(str(close["id"]), []).append(close)

    for tag in tags:
        tag_id = str(tag["id"])
        tag_pos = int(tag["position"])
        for close in closing_by_id.get(tag_id, []):
            close_pos = int(close["position"])
            if close_pos >= tag_pos:
                tag["explicit_end"] = close_pos
                tag["explicit_end_source"] = close["text"]
                break

    return "".join(clean_parts), tags, stripped_closing_tags, normalized_bare_tags


def _clean_reason_line(line: str) -> str:
    line = line.strip()
    if line.startswith("-"):
        line = line[1:].strip()
    return line


def _parse_reasons(reasons_text: str) -> dict[str, str]:
    """Parse `g01: reason`, `g01=VALUE: reason`, or header+bullet reason blocks."""
    reasons: dict[str, str] = {}
    current_id: str | None = None

    for raw_line in reasons_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        colon_match = REASON_COLON_PATTERN.match(line)
        if colon_match:
            current_id = colon_match.group(1)
            reason = colon_match.group(2).strip()
            if reason:
                reasons[current_id] = reason
            else:
                reasons.setdefault(current_id, "")
            continue

        header_match = REASON_HEADER_PATTERN.match(line)
        if header_match:
            current_id = header_match.group(1)
            reasons.setdefault(current_id, "")
            continue

        if current_id is not None:
            reason_piece = _clean_reason_line(line)
            if reason_piece:
                existing = reasons.get(current_id, "")
                reasons[current_id] = f"{existing} {reason_piece}".strip()

    return reasons


def _renumber_duplicate_tag_ids(tags: list[dict[str, Any]], reasons: dict[str, str]) -> list[dict[str, Any]]:
    """Ensure every tag occurrence has a unique id.

    The LLM sometimes reuses ids like m01 for multiple mask spans. That is bad for
    diagnostics and downstream event identity. Keep the first occurrence and
    renumber later duplicates by prefix, copying the original reason when needed.
    """
    original_ids = {str(tag.get("id", "")) for tag in tags}
    assigned: set[str] = set()
    renumbered: list[dict[str, Any]] = []

    def next_free_id(prefix: str) -> str:
        idx = 1
        while True:
            candidate = f"{prefix}{idx:02d}"
            if candidate not in assigned and candidate not in original_ids:
                return candidate
            idx += 1

    for tag in tags:
        old_id = str(tag["id"])
        if old_id not in assigned:
            assigned.add(old_id)
            continue

        prefix = str(tag["prefix"])
        new_id = next_free_id(prefix)
        tag["original_id"] = old_id
        tag["id"] = new_id
        tag["renumbered_duplicate_id"] = True
        assigned.add(new_id)

        if old_id in reasons and new_id not in reasons:
            reasons[new_id] = reasons[old_id]

        renumbered.append(
            {
                "old_id": old_id,
                "new_id": new_id,
                "type": tag.get("type"),
                "value": tag.get("value"),
                "position": tag.get("position"),
            }
        )

    return renumbered


def parse_performance_annotation(path: str | Path) -> dict[str, Any]:
    """
    Parse [ANALYZE], [ANNOTATION], [REASONS] and readable performance tags.

    Supported tags:
        g##  gaze state-change
        m##  JALI mask state-change
        h##  JALI heart state-change
        l##  lid_state state-change
        pb## performative blink anchor/local event
        bs## blink suppression state-change / gate
    """
    source_text = _read_text(path)
    sections, warnings = _parse_sections(source_text)
    annotation_text = sections.get("ANNOTATION", "")
    clean_transcript, tags, stripped_closing_tags, normalized_bare_tags = _strip_tags_and_collect(annotation_text)
    reasons = _parse_reasons(sections.get("REASONS", ""))
    renumbered_duplicate_tag_ids = _renumber_duplicate_tag_ids(tags, reasons)

    tag_ids = {tag["id"] for tag in tags}
    missing_reasons = [tag["id"] for tag in tags if not reasons.get(tag["id"], "").strip()]
    extra_reasons = [tag_id for tag_id in reasons if tag_id not in tag_ids]

    for tag in tags:
        tag["reason"] = reasons.get(tag["id"], "")

    diagnostics = {
        "warnings": warnings,
        "missing_reasons": missing_reasons,
        "extra_reasons": extra_reasons,
        "normalized_bare_tags": normalized_bare_tags,
        "renumbered_duplicate_tag_ids": renumbered_duplicate_tag_ids,
        "stripped_closing_tags": stripped_closing_tags,
        "tag_count": len(tags),
        "tag_type_counts": {
            tag_type: sum(1 for tag in tags if tag.get("type") == tag_type)
            for tag_type in sorted(set(TAG_TYPES.values()))
        },
    }

    return {
        "path": str(path),
        "source_text": source_text,
        "sections": sections,
        "analyze": sections.get("ANALYZE", ""),
        "annotation_text": annotation_text,
        "reasons_text": sections.get("REASONS", ""),
        "reasons": reasons,
        "clean_transcript": clean_transcript,
        "tags": tags,
        "diagnostics": diagnostics,
    }
