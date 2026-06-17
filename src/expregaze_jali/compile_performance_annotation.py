from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from expregaze_jali.actor_overlay_event_exporter import export_actor_overlay_events
from expregaze_jali.gaze_event_exporter import export_gaze_events
from expregaze_jali.jali_annotation_exporter import export_jali_annotation
from expregaze_jali.performance_annotation_parser import parse_performance_annotation
from expregaze_jali.performance_event_compiler import compile_state_change_events
from expregaze_jali.performance_event_resolver import load_words_jsonl, resolve_events_with_textgrid

DEFAULT_SEQUENCE_ID = "Jali_proto_candidate_001_ProfessorCrystal"
DEFAULT_LLM_PROCESS_DIR = Path("data/processed/gaze_script/llm_process")
DEFAULT_OUTPUT_DIR = Path("data/processed/gaze_script")
DEFAULT_WORDS_DIR = Path("data/processed/textgrid")


def _write_text(path: Path, text: str, *, overwrite: bool = True) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing file without --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any], *, overwrite: bool = True) -> None:
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2), overwrite=overwrite)


def _read_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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


def compile_outputs(
    *,
    annotation_path: Path,
    words_jsonl: Path,
    clip_name: str,
    output_dir: Path,
    llm_process_dir: Path,
    context_pack_path: Path | None = None,
    overwrite: bool = True,
) -> dict[str, Path]:
    parsed = parse_performance_annotation(annotation_path)
    compiled = compile_state_change_events(parsed)
    resolved = resolve_events_with_textgrid(compiled, load_words_jsonl(words_jsonl))
    resolved["diagnostics"] = {
        "missing_reasons": parsed.get("diagnostics", {}).get("missing_reasons", []),
        "parser_warnings": parsed.get("diagnostics", {}).get("warnings", []),
        "stripped_closing_tags": parsed.get("diagnostics", {}).get("stripped_closing_tags", []),
        **resolved.get("diagnostics", {}),
    }

    context_pack = _read_optional_json(context_pack_path)
    jali_text = export_jali_annotation(parsed, resolved)
    gaze_events = export_gaze_events(resolved, clip_name=clip_name, context_pack=context_pack)
    actor_overlay = export_actor_overlay_events(resolved, clip_name=clip_name)
    debug_text = _debug_payload(parsed, compiled, resolved)

    annotated_for_jali = output_dir / f"{clip_name}__annotated_for_jali.txt"
    gaze_events_json = output_dir / f"{clip_name}__gaze_events_resolved.json"
    actor_overlay_json = output_dir / f"{clip_name}__actor_overlay_events.json"
    debug_full_annotation = llm_process_dir / f"{clip_name}__debug_full_annotation.txt"

    _write_text(annotated_for_jali, jali_text, overwrite=overwrite)
    _write_json(gaze_events_json, gaze_events, overwrite=overwrite)
    _write_json(actor_overlay_json, actor_overlay, overwrite=overwrite)
    _write_text(debug_full_annotation, debug_text, overwrite=overwrite)

    return {
        "annotated_for_jali": annotated_for_jali,
        "gaze_events_json": gaze_events_json,
        "actor_overlay_json": actor_overlay_json,
        "debug_full_annotation": debug_full_annotation,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Step 03: compile readable actor annotation into JALI / Maya outputs. No LLM call.")
    parser.add_argument("--sequence-id", "--clip-name", dest="sequence_id", default=DEFAULT_SEQUENCE_ID)
    parser.add_argument("--annotation-path", type=Path, default=None)
    parser.add_argument("--words-jsonl", type=Path, default=None)
    parser.add_argument("--context-pack", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--llm-process-dir", type=Path, default=DEFAULT_LLM_PROCESS_DIR)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    clip = args.sequence_id
    annotation_path = args.annotation_path or args.llm_process_dir / f"{clip}__performance_annotation.txt"
    words_jsonl = args.words_jsonl or DEFAULT_WORDS_DIR / f"{clip}__words.jsonl"
    context_pack_path = args.context_pack or args.llm_process_dir / f"{clip}__context_pack.json"

    outputs = compile_outputs(
        annotation_path=annotation_path,
        words_jsonl=words_jsonl,
        clip_name=clip,
        output_dir=args.output_dir,
        llm_process_dir=args.llm_process_dir,
        context_pack_path=context_pack_path,
        overwrite=args.overwrite,
    )

    print(f"Annotation: {annotation_path}")
    print(f"Words: {words_jsonl}")
    print(f"Context pack: {context_pack_path if context_pack_path.exists() else 'missing'}")
    for label, path in outputs.items():
        print(f"{label}: {path}")
    print("LLM calls: 0")

    gaze_events = json.loads(outputs["gaze_events_json"].read_text(encoding="utf-8"))
    diagnostics = gaze_events.get("diagnostics", {})
    unresolved = diagnostics.get("unresolved_events", [])
    unresolved_targets = diagnostics.get("unresolved_targets", [])
    alignment_warnings = diagnostics.get("alignment_warnings", [])
    if unresolved:
        print(f"WARNING unresolved timing events: {len(unresolved)}")
    if unresolved_targets:
        print(f"WARNING targets needing resolution: {len(unresolved_targets)}")
    if alignment_warnings:
        print(f"WARNING alignment warnings: {len(alignment_warnings)}")


if __name__ == "__main__":
    main()
