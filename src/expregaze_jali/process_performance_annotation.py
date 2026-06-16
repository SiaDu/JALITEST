from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from expregaze_jali.textgrid_parser import parse_textgrid_words
from expregaze_jali.performance_annotation_parser import parse_performance_annotation
from expregaze_jali.performance_event_compiler import compile_state_change_events
from expregaze_jali.performance_event_resolver import resolve_events_with_textgrid
from expregaze_jali.jali_annotation_exporter import export_jali_annotation
from expregaze_jali.gaze_event_exporter import export_gaze_events
from expregaze_jali.eye_performance_event_exporter import export_eye_performance_events


def process_performance_annotation(
    script_path: str | Path,
    textgrid_path: str | Path,
    output_dir: str | Path,
    clip_name: str,
    fps: float = 30.0,
    clip_end_frame: float | None = None,
    regulatory_config: dict[str, Any] | None = None,
) -> dict:
    script_path = Path(script_path)
    textgrid_path = Path(textgrid_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    words = parse_textgrid_words(textgrid_path)
    parsed = parse_performance_annotation(script_path)
    events = compile_state_change_events(parsed)
    resolved = resolve_events_with_textgrid(events, words)

    jali_txt = export_jali_annotation(parsed, resolved)
    gaze_json = export_gaze_events(resolved, clip_name=clip_name)
    eye_json = export_eye_performance_events(
        resolved,
        clip_name=clip_name,
        fps=fps,
        clip_end_frame=clip_end_frame,
        regulatory_config=regulatory_config,
    )

    output_paths = {
        "annotated_for_jali": output_dir / f"{clip_name}__annotated_for_jali.txt",
        "gaze_events": output_dir / f"{clip_name}__gaze_events_resolved.json",
        "eye_performance_events": output_dir / f"{clip_name}__eye_performance_events_resolved.json",
        "debug_full_annotation": output_dir / f"{clip_name}__debug_full_annotation.txt",
        "resolved_all_events": output_dir / f"{clip_name}__performance_events_resolved.json",
    }

    output_paths["annotated_for_jali"].write_text(jali_txt, encoding="utf-8")
    output_paths["gaze_events"].write_text(
        json.dumps(gaze_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    output_paths["eye_performance_events"].write_text(
        json.dumps(eye_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    output_paths["debug_full_annotation"].write_text(
        parsed["source_text"],
        encoding="utf-8",
    )
    output_paths["resolved_all_events"].write_text(
        json.dumps(resolved, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "clip_name": clip_name,
        "script_path": str(script_path),
        "textgrid_path": str(textgrid_path),
        "output_paths": {key: str(value) for key, value in output_paths.items()},
        "parser_diagnostics": parsed.get("diagnostics", {}),
        "resolver_diagnostics": resolved.get("diagnostics", {}),
        "eye_diagnostics": eye_json.get("diagnostics", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile ExpreGaze readable performance annotation into JALI/gaze/eye outputs."
    )
    parser.add_argument("--script", required=True, help="Readable performance annotation txt.")
    parser.add_argument("--textgrid", required=True, help="JALI TextGrid path.")
    parser.add_argument("--out-dir", required=True, help="Output directory.")
    parser.add_argument("--clip-name", required=True, help="Clip basename.")
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--clip-end-frame", type=float, default=None)

    # Conservative defaults: only long-gap subtle blinks are generated.
    # Enable reset blinks explicitly after the performative blink prompt is stable.
    parser.add_argument("--regulatory-from-gaze", action="store_true")
    parser.add_argument("--regulatory-from-mask", action="store_true")
    parser.add_argument("--regulatory-from-heart", action="store_true")
    parser.add_argument("--regulatory-from-lid-state", action="store_true")
    parser.add_argument("--no-long-gap-regulatory-blinks", action="store_true")
    parser.add_argument("--regulatory-min-gap-frames", type=int, default=45)
    parser.add_argument("--long-gap-seconds", type=float, default=5.0)
    parser.add_argument("--gaze-blink-offset-frames", type=int, default=1)

    args = parser.parse_args()

    regulatory_config = {
        "generate_from_gaze": args.regulatory_from_gaze,
        "generate_from_mask": args.regulatory_from_mask,
        "generate_from_heart": args.regulatory_from_heart,
        "generate_from_lid_state": args.regulatory_from_lid_state,
        "generate_long_gap_blinks": not args.no_long_gap_regulatory_blinks,
        "min_gap_frames": args.regulatory_min_gap_frames,
        "long_gap_seconds": args.long_gap_seconds,
        "gaze_blink_offset_frames": args.gaze_blink_offset_frames,
    }

    result = process_performance_annotation(
        script_path=args.script,
        textgrid_path=args.textgrid,
        output_dir=args.out_dir,
        clip_name=args.clip_name,
        fps=args.fps,
        clip_end_frame=args.clip_end_frame,
        regulatory_config=regulatory_config,
    )

    print("[DONE] regenerated performance outputs")
    for key, value in result["output_paths"].items():
        print(f" - {key}: {value}")

    print("\n[DIAGNOSTICS]")
    print(json.dumps(
        {
            "parser": result["parser_diagnostics"],
            "resolver": result["resolver_diagnostics"],
            "eye": result["eye_diagnostics"],
        },
        indent=2,
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
