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
OPENING_TAG_PATTERN = re.compile(rf"<({TAG_ID_PATTERN})=([^<>]+)>")
CLOSING_TAG_PATTERN = re.compile(rf"</({TAG_ID_PATTERN})>")
ANY_TAG_PATTERN = re.compile(rf"</({TAG_ID_PATTERN})>|<({TAG_ID_PATTERN})=([^<>]+)>")
REASON_INLINE_PATTERN = re.compile(rf"^\s*({TAG_ID_PATTERN})(?:\s*=\s*[^:]+)?\s*:\s*(.*?)\s*$")
REASON_HEADER_PATTERN = re.compile(rf"^\s*({TAG_ID_PATTERN})(?:\s*=\s*(.*?))?\s*$")

TAG_TYPES = {
    "g": "gaze",
    "m": "mask",
    "h": "heart",
    "l": "lid_state",
    "pb": "performative_blink",
    "bs": "blink_suppression",
}

PREFIX_PATTERN = re.compile(r"^(pb|bs|[gmhl])\d+$")


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


def _attach_explicit_end_tags(tags: list[dict[str, Any]], closing_tags: list[dict[str, Any]]) -> None:
    """Attach matching closing-tag clean positions to opening tags.

    Closing tags are no longer just tolerated: they define explicit span ends for
    the matching opening tag. The clean transcript still strips them so TextGrid
    alignment sees only transcript words.
    """
    used_closes: set[int] = set()
    for tag in tags:
        for close_idx, close in enumerate(closing_tags):
            if close_idx in used_closes:
                continue
            if close["id"] != tag["id"]:
                continue
            if int(close["raw_start"]) < int(tag["raw_end"]):
                continue
            tag["explicit_end"] = int(close["position"])
            tag["explicit_raw_end"] = int(close["raw_end"])
            used_closes.add(close_idx)
            break


def _strip_tags_and_collect(annotation_text: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove readable tags from transcript and collect opening/closing metadata."""
    clean_parts: list[str] = []
    tags: list[dict[str, Any]] = []
    closing_tags: list[dict[str, Any]] = []
    clean_pos = 0
    raw_pos = 0

    for match in ANY_TAG_PATTERN.finditer(annotation_text):
        before = annotation_text[raw_pos : match.start()]
        clean_parts.append(before)
        clean_pos += len(before)

        if match.group(1):  # closing tag
            tag_id = match.group(1)
            closing_tags.append(
                {
                    "id": tag_id,
                    "text": match.group(0),
                    "raw_start": match.start(),
                    "raw_end": match.end(),
                    "position": clean_pos,
                    "order": len(closing_tags),
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
    clean_transcript = "".join(clean_parts)
    _attach_explicit_end_tags(tags, closing_tags)
    return clean_transcript, tags, closing_tags


def _clean_reason_line(line: str) -> str:
    line = line.strip()
    if line.startswith("-"):
        line = line[1:].strip()
    return line


def _parse_reasons(reasons_text: str) -> dict[str, str]:
    """Parse reason lines.

    Supported formats:
        g01: reason
        g01=GAZE-LISTENER: reason
        g01=GAZE-LISTENER
        - reason on following bullet line
    """
    reasons: dict[str, str] = {}
    current_id: str | None = None

    for raw_line in reasons_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        inline_match = REASON_INLINE_PATTERN.match(line)
        if inline_match:
            current_id = inline_match.group(1)
            reason = inline_match.group(2).strip()
            if reason:
                existing = reasons.get(current_id, "")
                reasons[current_id] = f"{existing} {reason}".strip()
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


def parse_performance_annotation(path: str | Path) -> dict[str, Any]:
    """
    Parse [ANALYZE], [ANNOTATION], [REASONS] and readable performance tags.

    Supported tags:
        g##  gaze
        m##  visible mask
        h##  hidden heart / internal undercurrent
        l##  lid_state
        pb## performative blink
        bs## blink suppression
    """
    source_text = _read_text(path)
    sections, warnings = _parse_sections(source_text)
    annotation_text = sections.get("ANNOTATION", "")
    clean_transcript, tags, closing_tags = _strip_tags_and_collect(annotation_text)
    reasons = _parse_reasons(sections.get("REASONS", ""))

    tag_ids = {tag["id"] for tag in tags}
    missing_reasons = [tag["id"] for tag in tags if not reasons.get(tag["id"], "").strip()]
    extra_reasons = [tag_id for tag_id in reasons if tag_id not in tag_ids]

    for tag in tags:
        tag["reason"] = reasons.get(tag["id"], "")

    diagnostics = {
        "warnings": warnings,
        "missing_reasons": missing_reasons,
        "extra_reasons": extra_reasons,
        "stripped_closing_tags": closing_tags,
        "closing_tag_count": len(closing_tags),
        "explicit_end_tag_count": sum(1 for tag in tags if "explicit_end" in tag),
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
