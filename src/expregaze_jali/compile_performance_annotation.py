from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from expregaze_jali.gaze_event_exporter import export_gaze_events
from expregaze_jali.jali_annotation_exporter import export_jali_annotation
from expregaze_jali.performance_annotation_parser import parse_performance_annotation
from expregaze_jali.performance_event_compiler import compile_state_change_events
from expregaze_jali.performance_event_resolver import load_words_jsonl, resolve_events_with_textgrid

DEFAULT_CLIP_NAME = "Jali_proto_candidate_001_ProfessorCrystal"
DEFAULT_ANNOTATION_DIR = Path("data/processed/gaze_script/llm_process")
DEFAULT_OUTPUT_DIR = Path("data/processed/gaze_script")
DEFAULT_ANNOTATION_PATH = DEFAULT_ANNOTATION_DIR / f"{DEFAULT_CLIP_NAME}__script.txt"
DEFAULT_WORDS_JSONL = Path("data/processed/textgrid") / f"{DEFAULT_CLIP_NAME}__words.jsonl"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _debug_payload(parsed: dict[str, Any], compiled: dict[str, Any], resolved: dict[str, Any]) -> str:
    summary = {
        "annotation_path": parsed.get("path"),
        "tag_count": len(parsed.get("tags", [])),
        "event_count": len(compiled.get("events", [])),
        "diagnostics": {
            "parser": parsed.get("diagnostics", {}),
            "resolver": resolved.get("diagnostics", {}),
        },
    }
    return "\n".join(
        [
            "[SUMMARY]",
            json.dumps(summary, ensure_ascii=False, indent=2),
            "",
            "[CLEAN_TRANSCRIPT]",
            parsed.get("clean_transcript", ""),
            "",
            "[FULL_ANNOTATION]",
            parsed.get("source_text", ""),
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile readable ExpreGaze-JALI performance annotation.")
    parser.add_argument("--annotation-path", type=Path, default=DEFAULT_ANNOTATION_PATH)
    parser.add_argument("--words-jsonl", type=Path, default=DEFAULT_WORDS_JSONL)
    parser.add_argument("--clip-name", type=str, default=DEFAULT_CLIP_NAME)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--annotated-for-jali", type=Path, default=None)
    parser.add_argument("--gaze-events-json", type=Path, default=None)
    parser.add_argument("--debug-full-annotation", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    annotated_for_jali = args.annotated_for_jali or output_dir / f"{args.clip_name}__annotated_for_jali.txt"
    gaze_events_json = args.gaze_events_json or output_dir / f"{args.clip_name}__gaze_events_resolved.json"
    debug_full_annotation = args.debug_full_annotation or output_dir / f"{args.clip_name}__debug_full_annotation.txt"

    parsed = parse_performance_annotation(args.annotation_path)
    compiled = compile_state_change_events(parsed)
    resolved = resolve_events_with_textgrid(compiled, load_words_jsonl(args.words_jsonl))
    resolved["diagnostics"] = {
        "missing_reasons": parsed.get("diagnostics", {}).get("missing_reasons", []),
        "parser_warnings": parsed.get("diagnostics", {}).get("warnings", []),
        **resolved.get("diagnostics", {}),
    }
    jali_text = export_jali_annotation(parsed, resolved)
    gaze_events = export_gaze_events(resolved, clip_name=args.clip_name)
    debug_text = _debug_payload(parsed, compiled, resolved)

    _write_text(annotated_for_jali, jali_text)
    _write_json(gaze_events_json, gaze_events)
    _write_text(debug_full_annotation, debug_text)

    print(f"Annotation: {args.annotation_path}")
    print(f"Words: {args.words_jsonl}")
    print(f"JALI text: {annotated_for_jali}")
    print(f"Gaze events: {gaze_events_json}")
    print(f"Debug: {debug_full_annotation}")
    print(f"Gaze event count: {len(gaze_events['events'])}")
    unresolved = gaze_events.get("diagnostics", {}).get("unresolved_events", [])
    if unresolved:
        print(f"Unresolved events: {len(unresolved)}")


if __name__ == "__main__":
    main()
