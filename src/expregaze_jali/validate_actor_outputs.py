from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from expregaze_jali.performance_annotation_parser import parse_performance_annotation

DEFAULT_SEQUENCE_ID = "Jali_proto_candidate_001_ProfessorCrystal"
DEFAULT_LLM_PROCESS_DIR = Path("data/processed/gaze_script/llm_process")
DEFAULT_OUTPUT_DIR = Path("data/processed/gaze_script")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _status_line(level: str, message: str) -> str:
    return f"{level}: {message}"


def validate_outputs(
    *,
    sequence_id: str,
    llm_process_dir: Path,
    output_dir: Path,
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    lines: list[str] = []

    annotation_path = llm_process_dir / f"{sequence_id}__performance_annotation.txt"
    meta_path = llm_process_dir / f"{sequence_id}__llm_response_meta.json"
    debug_path = llm_process_dir / f"{sequence_id}__debug_full_annotation.txt"
    jali_path = output_dir / f"{sequence_id}__annotated_for_jali.txt"
    gaze_path = output_dir / f"{sequence_id}__gaze_events_resolved.json"
    overlay_path = output_dir / f"{sequence_id}__actor_overlay_events.json"

    for label, path in [
        ("performance annotation", annotation_path),
        ("LLM meta", meta_path),
        ("JALI annotation", jali_path),
        ("gaze events", gaze_path),
        ("actor overlay", overlay_path),
    ]:
        if path.exists():
            lines.append(_status_line("OK", f"found {label}: {path}"))
        else:
            errors.append(f"missing {label}: {path}")

    if annotation_path.exists():
        parsed = parse_performance_annotation(annotation_path)
        section_warnings = parsed.get("diagnostics", {}).get("warnings", [])
        if section_warnings:
            warnings.extend(section_warnings)
        else:
            lines.append(_status_line("OK", "annotation has [ANALYZE] [ANNOTATION] [REASONS]"))

        stripped = parsed.get("diagnostics", {}).get("stripped_closing_tags", [])
        if stripped:
            lines.append(_status_line("OK", f"parser stripped closing tags: {len(stripped)}"))

        missing_reasons = parsed.get("diagnostics", {}).get("missing_reasons", [])
        if missing_reasons:
            warnings.append(f"tags missing reasons: {missing_reasons}")

    if meta_path.exists():
        meta = _read_json(meta_path)
        status = meta.get("status")
        if status == "completed":
            lines.append(_status_line("OK", "LLM status completed"))
        else:
            warnings.append(f"LLM status is {status!r}")

    if gaze_path.exists():
        gaze = _read_json(gaze_path)
        events = gaze.get("events", [])
        if events:
            lines.append(_status_line("OK", f"gaze event count: {len(events)}"))
        else:
            warnings.append("no gaze events exported")

        missing_time = [e.get("id") for e in events if not e.get("resolved_time")]
        if missing_time:
            warnings.append(f"gaze events missing resolved_time: {missing_time}")
        else:
            lines.append(_status_line("OK", "all gaze events have resolved_time"))

        listener_events = [e for e in events if e.get("target") == "LISTENER"]
        unresolved_listener = [e.get("id") for e in listener_events if not e.get("target_label")]
        if listener_events and not unresolved_listener:
            labels = sorted({e.get("target_label") for e in listener_events if e.get("target_label")})
            lines.append(_status_line("OK", f"LISTENER resolved to: {labels}"))
        elif unresolved_listener:
            warnings.append(f"LISTENER targets needing resolution: {unresolved_listener}")

        diagnostics = gaze.get("diagnostics", {})
        alignment_warnings = diagnostics.get("alignment_warnings", [])
        if alignment_warnings:
            warnings.append(f"alignment warnings: {len(alignment_warnings)}")
        else:
            lines.append(_status_line("OK", "no TextGrid alignment warnings"))

        unresolved_targets = diagnostics.get("unresolved_targets", [])
        generic_targets = diagnostics.get("generic_targets", [])
        if generic_targets:
            warnings.append(f"generic gaze targets present: {len(generic_targets)}")
        if unresolved_targets:
            warnings.append(f"gaze targets needing resolution: {len(unresolved_targets)}")
        else:
            lines.append(_status_line("OK", "no gaze targets need manual resolution"))

    if overlay_path.exists():
        overlay = _read_json(overlay_path)
        lines.append(_status_line("OK", f"actor overlay event count: {len(overlay.get('events', []))}"))

    if debug_path.exists():
        lines.append(_status_line("OK", f"debug file: {debug_path}"))

    return lines + [_status_line("WARNING", w) for w in warnings] + [_status_line("ERROR", e) for e in errors], errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Step 04: validate actor annotation outputs. No LLM call.")
    parser.add_argument("--sequence-id", default=DEFAULT_SEQUENCE_ID)
    parser.add_argument("--llm-process-dir", type=Path, default=DEFAULT_LLM_PROCESS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when warnings or errors are present.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    lines, errors = validate_outputs(
        sequence_id=args.sequence_id,
        llm_process_dir=args.llm_process_dir,
        output_dir=args.output_dir,
    )
    for line in lines:
        print(line)
    print("LLM calls: 0")

    has_warnings = any(line.startswith("WARNING:") for line in lines)
    if errors or (args.strict and has_warnings):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
