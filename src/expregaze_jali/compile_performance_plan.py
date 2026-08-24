"""Compile an edited canonical Performance Plan into deterministic Maya artifacts.

This HCI path never reads the original actor annotation.  Canonical character
spans in ``performance_plan.json`` and the participant's exact script are the
only semantic source of truth.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable

from expregaze_jali.eye_performance_event_exporter import export_eye_performance_events
from expregaze_jali.gaze_event_exporter import export_gaze_events
from expregaze_jali.jali_annotation_exporter import export_jali_annotation
from expregaze_jali.performance_event_resolver import (
    load_words_jsonl,
    resolve_events_with_textgrid,
)
from expregaze_jali.performance_plan_schema import SCHEMA_VERSION, assert_no_timing_fields
from expregaze_jali.textgrid_parser import parse_textgrid_words


@dataclass(frozen=True)
class TimingAlignment:
    path: Path
    kind: str
    words: list[dict[str, Any]]


@dataclass(frozen=True)
class PlanAnimationArtifacts:
    annotated_for_jali: Path
    gaze_events: Path
    eye_performance_events: Path
    head_events: Path
    resolved_events: Path
    runtime_transcript: Path
    debug_summary: Path
    manifest: Path


def _single_candidate(paths: Iterable[Path], label: str) -> Path | None:
    candidates = sorted({path.resolve() for path in paths if path.is_file()})
    if len(candidates) > 1:
        rendered = "\n".join(f"- {path}" for path in candidates)
        raise ValueError(f"Multiple {label} files found; keep exactly one:\n{rendered}")
    return candidates[0] if candidates else None


def _validate_words(words: list[dict[str, Any]], source: Path) -> None:
    if not words:
        raise ValueError(f"Timing alignment contains no words: {source}")
    for index, word in enumerate(words, start=1):
        if not isinstance(word, dict) or not {"start", "end"} <= set(word):
            raise ValueError(f"Invalid timing word {index} in {source}: start/end are required.")
        if "word" not in word and "norm" not in word:
            raise ValueError(f"Invalid timing word {index} in {source}: word or norm is required.")


def discover_timing_alignment(audio_folder: str | Path) -> TimingAlignment:
    """Discover one explicit words JSONL or TextGrid in an HCI audio folder."""
    folder = Path(audio_folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Input Audio Folder does not exist: {folder}")

    words_path = _single_candidate(folder.glob("*words*.jsonl"), "words JSONL")
    if words_path is not None:
        words = load_words_jsonl(words_path)
        _validate_words(words, words_path)
        return TimingAlignment(words_path, "words_jsonl", words)

    textgrid_path = _single_candidate(
        [*folder.glob("*.TextGrid"), *folder.glob("*.textgrid")],
        "TextGrid",
    )
    if textgrid_path is not None:
        words = parse_textgrid_words(textgrid_path)
        _validate_words(words, textgrid_path)
        return TimingAlignment(textgrid_path, "textgrid", words)

    raise FileNotFoundError("No timing alignment file found in the Input Audio Folder.")


def _load_plan(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Performance Plan JSON must contain an object.")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Performance Plan schema_version must be {SCHEMA_VERSION!r}.")
    if not isinstance(value.get("events"), list):
        raise ValueError("Performance Plan must contain an events list.")
    assert_no_timing_fields(value)
    return value


def _span_bounds(span: dict[str, Any], transcript: str, label: str) -> tuple[int, int]:
    try:
        start = int(span["char_start"])
        end = int(span["char_end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} requires integer char_start/char_end.") from exc
    if start < 0 or end <= start or end > len(transcript):
        raise ValueError(
            f"{label} has invalid canonical span [{start}, {end}) for script length {len(transcript)}."
        )
    return start, end


def _reason(rows: Any, source_tag: str) -> str:
    values = rows if isinstance(rows, list) else [rows]
    for row in values:
        if isinstance(row, dict) and str(row.get("source_tag") or "") == source_tag:
            return str(row.get("reason") or "")
    return ""


def _compiled_event(
    *,
    span: dict[str, Any],
    event_type: str,
    value: str,
    transcript: str,
    reason_rows: Any,
    order: int,
    label: str,
) -> dict[str, Any]:
    start, end = _span_bounds(span, transcript, label)
    source_tag = str(span.get("source_tag") or f"plan_{event_type}_{order + 1}")
    return {
        "id": f"{source_tag}__plan_{order + 1:03d}",
        "source_tag": source_tag,
        "type": event_type,
        "value": str(value),
        "text": transcript[start:end],
        "reason": _reason(reason_rows, source_tag),
        "span": {"start": start, "end": end, "raw_start": start, "raw_end": end},
        "order": order,
        "canonical_interval": True,
    }


def compile_plan_events(plan: dict[str, Any], transcript: str) -> dict[str, Any]:
    """Translate canonical plan spans directly into the established event shape."""
    if not transcript.strip():
        raise ValueError("Exact input script is required.")

    events: list[dict[str, Any]] = []
    order = 0
    for event_index, plan_event in enumerate(plan.get("events", []), start=1):
        if not isinstance(plan_event, dict):
            raise ValueError(f"Performance Plan event {event_index} must be an object.")
        event_span = plan_event.get("span")
        if not isinstance(event_span, dict):
            raise ValueError(f"Performance Plan event {event_index} requires a span.")
        start, end = _span_bounds(event_span, transcript, f"event {event_index}")
        expected = str(event_span.get("text") or "")
        if transcript[start:end] != expected:
            raise ValueError(
                f"Input Script does not match canonical event {event_index} at [{start}, {end})."
            )

        rationale = plan_event.get("rationale") if isinstance(plan_event.get("rationale"), dict) else {}
        intent_value = str(plan_event.get("intent") or "")
        if intent_value:
            intent_span = {
                "source_tag": plan_event.get("source_intent_tag") or f"intent_{event_index}",
                "char_start": start,
                "char_end": end,
            }
            events.append(
                _compiled_event(
                    span=intent_span,
                    event_type="intent",
                    value=intent_value,
                    transcript=transcript,
                    reason_rows=rationale.get("intent"),
                    order=order,
                    label=f"event {event_index} intent",
                )
            )
            order += 1

        affect = plan_event.get("affect") if isinstance(plan_event.get("affect"), dict) else {}
        affect_reasons = rationale.get("affect") if isinstance(rationale.get("affect"), dict) else {}
        channel_specs = (
            (affect.get("visible", []), "mask", affect_reasons.get("visible"), "visible affect"),
            (affect.get("hidden", []), "heart", affect_reasons.get("hidden"), "hidden affect"),
            (plan_event.get("gaze", []), "gaze", rationale.get("gaze"), "gaze"),
            (plan_event.get("head", []), "head_involvement", rationale.get("head"), "head"),
            (plan_event.get("lid_state", []), "lid_state", rationale.get("lid_state"), "lid"),
        )
        blink = plan_event.get("blink") if isinstance(plan_event.get("blink"), dict) else {}
        blink_reasons = rationale.get("blink") if isinstance(rationale.get("blink"), dict) else {}
        channel_specs += (
            (blink.get("performative", []), "performative_blink", blink_reasons.get("performative"), "blink"),
            (blink.get("suppression", []), "blink_suppression", blink_reasons.get("suppression"), "blink suppression"),
        )

        for spans, event_type, reason_rows, label in channel_specs:
            for span_index, span in enumerate(spans if isinstance(spans, list) else [], start=1):
                if not isinstance(span, dict):
                    raise ValueError(f"Event {event_index} {label} span {span_index} must be an object.")
                events.append(
                    _compiled_event(
                        span=span,
                        event_type=event_type,
                        value=str(span.get("value") or ""),
                        transcript=transcript,
                        reason_rows=reason_rows,
                        order=order,
                        label=f"event {event_index} {label} span {span_index}",
                    )
                )
                order += 1

    events.sort(key=lambda item: (int(item["span"]["start"]), int(item["order"])))
    result: dict[str, Any] = {
        "clean_transcript": transcript,
        "events": events,
        "diagnostics": {},
        "source": "canonical_performance_plan",
    }
    for event_type in {str(item["type"]) for item in events}:
        result[event_type] = [item for item in events if item["type"] == event_type]
    return result


def _clip_end_seconds(words: list[dict[str, Any]]) -> float:
    return max(float(word["end"]) for word in words)


def _insert_gaze_resets(gaze: dict[str, Any], clip_end_sec: float) -> None:
    semantic = sorted(gaze.get("events", []), key=lambda item: float(item["resolved_time"]["start"]))
    resets: list[dict[str, Any]] = []
    for index, event in enumerate(semantic):
        resolved = event.get("resolved_time")
        if not resolved:
            continue
        end = float(resolved["end"])
        next_start = (
            float(semantic[index + 1]["resolved_time"]["start"])
            if index + 1 < len(semantic) and semantic[index + 1].get("resolved_time")
            else clip_end_sec
        )
        if next_start > end:
            resets.append(
                {
                    "id": f"{event.get('id')}_canonical_end",
                    "type": "gaze",
                    "mode": "GAZE",
                    "target": "__BASE__",
                    "text": "",
                    "reason": "Return to the unmapped base gaze after the canonical span ends.",
                    "span": {"start": event.get("span", {}).get("end"), "end": event.get("span", {}).get("end")},
                    "resolved_time": {"start": end, "end": next_start, "source": "canonical_span_end"},
                    "canonical_reset": True,
                }
            )
    gaze["events"] = sorted(
        [*semantic, *resets], key=lambda item: float(item["resolved_time"]["start"])
    )
    gaze.setdefault("diagnostics", {})["canonical_gaze_reset_count"] = len(resets)


def _canonical_suppression_intervals(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    for event in resolved.get("events", []):
        if event.get("type") != "blink_suppression" or not event.get("resolved_time"):
            continue
        mode = str(event.get("value") or "").strip().upper()
        if mode not in {"SUPPRESS", "SUPPRESS_BLINK", "ON"}:
            continue
        timing = event["resolved_time"]
        intervals.append(
            {
                "id": event.get("id"),
                "type": "blink_suppression",
                "mode": "SUPPRESS",
                "detail": "canonical_interval",
                "start": float(timing["start"]),
                "end": float(timing["end"]),
                "text": event.get("text", ""),
                "reason": event.get("reason", ""),
                "source_event_id": event.get("id"),
            }
        )
    return intervals


def _insert_lid_resets(eye: dict[str, Any], clip_end_sec: float) -> None:
    semantic = sorted(
        eye.get("lid_state_events", []), key=lambda item: float(item["resolved_time"]["start"])
    )
    resets: list[dict[str, Any]] = []
    for index, event in enumerate(semantic):
        resolved = event.get("resolved_time")
        if not resolved:
            continue
        end = float(resolved["end"])
        next_start = (
            float(semantic[index + 1]["resolved_time"]["start"])
            if index + 1 < len(semantic) and semantic[index + 1].get("resolved_time")
            else clip_end_sec
        )
        if next_start > end:
            resets.append(
                {
                    "id": f"{event.get('id')}_canonical_end",
                    "type": "lid_state",
                    "value": 0,
                    "text": "",
                    "reason": "Restore the default lid state after the canonical span ends.",
                    "span": {"start": event.get("span", {}).get("end"), "end": event.get("span", {}).get("end")},
                    "resolved_time": {"start": end, "end": next_start, "source": "canonical_span_end"},
                    "canonical_reset": True,
                }
            )
    eye["lid_state_events"] = sorted(
        [*semantic, *resets], key=lambda item: float(item["resolved_time"]["start"])
    )
    eye.setdefault("diagnostics", {})["canonical_lid_reset_count"] = len(resets)


def _write_text(path: Path, value: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing file without --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: dict[str, Any], overwrite: bool) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", overwrite)


def compile_performance_plan(
    *,
    performance_plan_path: str | Path,
    script: str,
    audio_folder: str | Path,
    output_dir: str | Path,
    fps: float = 30.0,
    overwrite: bool = False,
) -> dict[str, Any]:
    plan_path = Path(performance_plan_path)
    output = Path(output_dir)
    plan = _load_plan(plan_path)
    timing = discover_timing_alignment(audio_folder)
    compiled = compile_plan_events(plan, script)
    resolved = resolve_events_with_textgrid(compiled, timing.words)
    alignment_warnings = resolved.get("diagnostics", {}).get("alignment_warnings", [])
    if alignment_warnings:
        raise ValueError(
            "Input Script does not match the timing alignment: "
            + str(alignment_warnings[0])
        )
    unresolved = resolved.get("diagnostics", {}).get("unresolved_events", [])
    if unresolved:
        ids = ", ".join(str(item.get("id") or "?") for item in unresolved[:10])
        raise ValueError(
            "Timing alignment could not resolve canonical event(s): "
            f"{ids}. Check that Input Script matches the alignment transcript."
        )
    clip_end_sec = _clip_end_seconds(timing.words)
    clip_end_frame = clip_end_sec * float(fps)
    clip_name = str(plan.get("sequence_id") or plan_path.stem)

    jali = export_jali_annotation({"clean_transcript": script}, resolved)
    gaze = export_gaze_events(resolved, clip_name=clip_name)
    _insert_gaze_resets(gaze, clip_end_sec)
    eye = export_eye_performance_events(
        resolved,
        clip_name=clip_name,
        fps=float(fps),
        clip_end_frame=clip_end_frame,
        generate_regulatory=False,
    )
    eye["blink_suppression_events"] = _canonical_suppression_intervals(resolved)
    eye["diagnostics"]["blink_suppression_count"] = len(eye["blink_suppression_events"])
    _insert_lid_resets(eye, clip_end_sec)

    head = {
        "clip_name": clip_name,
        "events": [event for event in resolved.get("events", []) if event.get("type") == "head_involvement"],
        "diagnostics": {
            "event_count": sum(1 for event in resolved.get("events", []) if event.get("type") == "head_involvement"),
            "maya_apply_status": "not_implemented",
            "warning": "Head involvement is compiled explicitly, but no Maya head applier exists yet.",
        },
    }

    artifacts = PlanAnimationArtifacts(
        annotated_for_jali=output / "annotated_for_jali.txt",
        gaze_events=output / "gaze_events_resolved.json",
        eye_performance_events=output / "eye_performance_events.json",
        head_events=output / "head_events_resolved.json",
        resolved_events=output / "semantic_events_resolved.json",
        runtime_transcript=output / "jali_runtime_transcript.txt",
        debug_summary=output / "compile_from_plan_debug.txt",
        manifest=output / "animation_manifest.json",
    )
    for path in artifacts.__dict__.values():
        if path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing file without --overwrite: {path}")

    debug = {
        "source": "canonical_performance_plan",
        "performance_plan": str(plan_path.resolve()),
        "original_performance_annotation_used": False,
        "timing_source": {"kind": timing.kind, "path": str(timing.path)},
        "fps": float(fps),
        "clip_end_frame": clip_end_frame,
        "event_counts": {
            event_type: sum(1 for event in resolved.get("events", []) if event.get("type") == event_type)
            for event_type in (
                "intent", "mask", "heart", "gaze", "head_involvement",
                "lid_state", "performative_blink", "blink_suppression",
            )
        },
        "resolver_diagnostics": resolved.get("diagnostics", {}),
        "head_apply_warning": head["diagnostics"]["warning"],
    }
    manifest = {
        "schema_version": "hci_animation_manifest_v0",
        "source": "canonical_performance_plan",
        "performance_plan": str(plan_path.resolve()),
        "timing_source": {"kind": timing.kind, "path": str(timing.path)},
        "fps": float(fps),
        "clip_end_frame": clip_end_frame,
        "artifacts": {
            key: str(path.resolve())
            for key, path in artifacts.__dict__.items()
            if key != "manifest"
        },
        "warnings": [head["diagnostics"]["warning"]] if head["events"] else [],
    }

    _write_text(artifacts.annotated_for_jali, jali, overwrite)
    _write_json(artifacts.gaze_events, gaze, overwrite)
    _write_json(artifacts.eye_performance_events, eye, overwrite)
    _write_json(artifacts.head_events, head, overwrite)
    _write_json(artifacts.resolved_events, resolved, overwrite)
    _write_text(artifacts.runtime_transcript, script, overwrite)
    _write_text(artifacts.debug_summary, json.dumps(debug, ensure_ascii=False, indent=2) + "\n", overwrite)
    _write_json(artifacts.manifest, manifest, overwrite)

    print(f"Source Performance Plan: {plan_path}", flush=True)
    print(f"Timing: {timing.kind} {timing.path}", flush=True)
    print(f"Animation manifest: {artifacts.manifest}", flush=True)
    print("Original performance annotation used: no", flush=True)
    if head["events"]:
        print(f"WARNING: {head['diagnostics']['warning']}", flush=True)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile an edited canonical Performance Plan into HCI animation artifacts."
    )
    parser.add_argument("--performance-plan", type=Path, required=True)
    parser.add_argument("--script-file", type=Path, required=True)
    parser.add_argument("--audio-folder", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    compile_performance_plan(
        performance_plan_path=args.performance_plan,
        script=args.script_file.read_text(encoding="utf-8"),
        audio_folder=args.audio_folder,
        output_dir=args.output_dir,
        fps=args.fps,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
