from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SECTION_PATTERN = re.compile(r"^\[(ANALYZE|ANNOTATION|REASONS)\]\s*$", re.MULTILINE)
TAG_PATTERN = re.compile(r"<([gmh]\d+)=([^<>]+)>")
REASON_PATTERN = re.compile(r"^\s*([gmh]\d+)\s*:\s*(.*?)\s*$")

TAG_TYPES = {
    "g": "gaze",
    "m": "mask",
    "h": "heart",
}


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


def _strip_tags_and_collect(annotation_text: str) -> tuple[str, list[dict[str, Any]]]:
    clean_parts: list[str] = []
    tags: list[dict[str, Any]] = []
    clean_pos = 0
    raw_pos = 0

    for order, match in enumerate(TAG_PATTERN.finditer(annotation_text)):
        before = annotation_text[raw_pos : match.start()]
        clean_parts.append(before)
        clean_pos += len(before)

        tag_id = match.group(1)
        tags.append(
            {
                "id": tag_id,
                "type": TAG_TYPES[tag_id[0]],
                "value": match.group(2).strip(),
                "position": clean_pos,
                "raw_start": match.start(),
                "raw_end": match.end(),
                "order": order,
            }
        )
        raw_pos = match.end()

    tail = annotation_text[raw_pos:]
    clean_parts.append(tail)
    return "".join(clean_parts), tags


def _parse_reasons(reasons_text: str) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for line in reasons_text.splitlines():
        match = REASON_PATTERN.match(line)
        if match:
            reasons[match.group(1)] = match.group(2)
    return reasons


def parse_performance_annotation(path: str | Path) -> dict[str, Any]:
    """
    Parse [ANALYZE], [ANNOTATION], [REASONS] and state-change tags.

    Tags are recorded at their character position in the clean transcript, after
    removing all readable annotation tags.
    """
    source_text = _read_text(path)
    sections, warnings = _parse_sections(source_text)
    annotation_text = sections.get("ANNOTATION", "")
    clean_transcript, tags = _strip_tags_and_collect(annotation_text)
    reasons = _parse_reasons(sections.get("REASONS", ""))

    missing_reasons = [tag["id"] for tag in tags if tag["id"] not in reasons]
    extra_reasons = [tag_id for tag_id in reasons if tag_id not in {tag["id"] for tag in tags}]

    for tag in tags:
        tag["reason"] = reasons.get(tag["id"], "")

    diagnostics = {
        "warnings": warnings,
        "missing_reasons": missing_reasons,
        "extra_reasons": extra_reasons,
        "tag_count": len(tags),
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

