from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import yaml

from expregaze_jali.actor_context_builder import (
    build_actor_context_pack,
    find_candidate,
    load_full_context_window,
)
from expregaze_jali.actor_overlay_event_exporter import export_actor_overlay_events
from expregaze_jali.actor_prompt_builder import (
    build_actor_annotation_prompt,
    get_capability_profile,
    load_extra_config_texts,
    load_prompt_template,
)
from expregaze_jali.gaze_event_exporter import export_gaze_events
from expregaze_jali.jali_annotation_exporter import export_jali_annotation
from expregaze_jali.performance_annotation_parser import parse_performance_annotation
from expregaze_jali.performance_event_compiler import compile_state_change_events
from expregaze_jali.performance_event_resolver import load_words_jsonl, resolve_events_with_textgrid

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv is optional at runtime
    load_dotenv = None

try:
    from openai import APIConnectionError, OpenAI
except Exception:  # pragma: no cover - handled in _call_openai
    OpenAI = None  # type: ignore[assignment]
    APIConnectionError = None  # type: ignore[assignment]


DEFAULT_SEQUENCE_ID = "Jali_proto_candidate_001_ProfessorCrystal"
DEFAULT_CANDIDATES = Path("data/processed/candidate_sequences/Jali_proto_candidate_sequences.jsonl")
DEFAULT_FULL_CONTEXT = Path("data/processed/full_context/tt0032138__full_context.csv")
DEFAULT_TEMPLATE = Path("prompts/actor_performance_annotation_prompt_v2.md")
DEFAULT_BASE_CONFIG = Path("configs/base.yaml")
DEFAULT_JALI_OPTIONS = Path("configs/jali_emotion_options.yaml")
DEFAULT_LLM_PROCESS_DIR = Path("data/processed/gaze_script/llm_process")
DEFAULT_COMPILED_OUTPUT_DIR = Path("data/processed/gaze_script")
DEFAULT_WORDS_DIR = Path("data/processed/textgrid")


def _read_yaml(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Config file not found: {file_path}")
    data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {file_path}")
    return data


def _load_llm_config(base_config: str | Path) -> dict[str, Any]:
    config = _read_yaml(base_config)
    llm = config.get("llm") or {}
    if not isinstance(llm, dict):
        raise ValueError("base config field `llm` must be a mapping")
    return llm


def _write_text(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing file without --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any], *, overwrite: bool) -> None:
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2), overwrite=overwrite)


def _coerce_temperature(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _coerce_max_output_tokens(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _coerce_positive_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    parsed = int(value)
    return max(parsed, 1)


def _coerce_positive_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    parsed = float(value)
    return max(parsed, 0.0)


def _supports_temperature(model: str) -> bool:
    return not model.lower().startswith("gpt-5")


def _build_openai_request(prompt: str, llm_config: dict[str, Any]) -> dict[str, Any]:
    model = str(llm_config.get("model") or "gpt-5-mini")
    temperature = _coerce_temperature(llm_config.get("temperature"))
    max_output_tokens = _coerce_max_output_tokens(llm_config.get("max_output_tokens"))

    request: dict[str, Any] = {
        "model": model,
        "input": prompt,
    }
    if temperature is not None and _supports_temperature(model):
        request["temperature"] = temperature
    elif temperature is not None:
        print(f"Skipping unsupported temperature parameter for model {model!r}.")
    if max_output_tokens is not None:
        request["max_output_tokens"] = max_output_tokens
    return request


def _response_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    # Fallback for SDK/object variants that do not expose output_text.
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def _response_meta(response: Any, *, model: str, prompt_path: Path, output_path: Path) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    return {
        "response_id": getattr(response, "id", None),
        "model": model,
        "prompt_path": str(prompt_path),
        "annotation_path": str(output_path),
        "usage": usage.model_dump() if hasattr(usage, "model_dump") else usage,
        "status": getattr(response, "status", None),
        "created_at": getattr(response, "created_at", None),
    }


def _call_openai(prompt: str, llm_config: dict[str, Any]) -> Any:
    if OpenAI is None:
        raise RuntimeError("openai package is not installed. Run: uv sync  # or pip install openai")

    if load_dotenv is not None:
        load_dotenv()

    api_key_env = str(llm_config.get("api_key_env") or "OPENAI_API_KEY")
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(
            f"Missing API key environment variable: {api_key_env}. "
            f"Example: export {api_key_env}='sk-proj-...'"
        )

    max_retries = _coerce_positive_int(llm_config.get("max_retries"), 3)
    retry_sleep_sec = _coerce_positive_float(llm_config.get("request_sleep_sec"), 2.0)
    request = _build_openai_request(prompt, llm_config)

    client = OpenAI(api_key=api_key)
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return client.responses.create(**request)
        except Exception as exc:
            is_connection_error = APIConnectionError is not None and isinstance(exc, APIConnectionError)
            if not is_connection_error:
                raise
            last_exc = exc
            if attempt < max_retries:
                print(
                    f"OpenAI connection failed; retrying {attempt}/{max_retries - 1} "
                    f"in {retry_sleep_sec:g}s: {exc}"
                )
                time.sleep(retry_sleep_sec)

    proxy_hint = (
        "OpenAI API connection failed after retries. This is a network/TLS issue, "
        "not an API-key or package-install issue. If Windows uses a proxy/VPN, WSL "
        "may need proxy variables, for example:\n"
        "  export HTTPS_PROXY='http://127.0.0.1:PORT'\n"
        "  export HTTP_PROXY='http://127.0.0.1:PORT'\n"
        "  export ALL_PROXY='socks5://127.0.0.1:PORT'\n"
        "Then rerun scripts/run_actor_annotator.sh from the same shell."
    )
    raise RuntimeError(proxy_hint) from last_exc


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


def _compile_outputs(
    *,
    annotation_path: Path,
    words_jsonl: Path,
    clip_name: str,
    output_dir: Path,
    overwrite: bool,
) -> dict[str, Path]:
    parsed = parse_performance_annotation(annotation_path)
    compiled = compile_state_change_events(parsed)
    resolved = resolve_events_with_textgrid(compiled, load_words_jsonl(words_jsonl))
    resolved["diagnostics"] = {
        "missing_reasons": parsed.get("diagnostics", {}).get("missing_reasons", []),
        "parser_warnings": parsed.get("diagnostics", {}).get("warnings", []),
        **resolved.get("diagnostics", {}),
    }

    jali_text = export_jali_annotation(parsed, resolved)
    gaze_events = export_gaze_events(resolved, clip_name=clip_name)
    actor_overlay = export_actor_overlay_events(resolved, clip_name=clip_name)
    debug_text = _debug_payload(parsed, compiled, resolved)

    annotated_for_jali = output_dir / f"{clip_name}__annotated_for_jali.txt"
    gaze_events_json = output_dir / f"{clip_name}__gaze_events_resolved.json"
    actor_overlay_json = output_dir / f"{clip_name}__actor_overlay_events.json"
    debug_full_annotation = output_dir / f"{clip_name}__debug_full_annotation.txt"

    _write_text(annotated_for_jali, jali_text, overwrite=overwrite)
    _write_json(gaze_events_json, gaze_events, overwrite=overwrite)
    _write_json(actor_overlay_json, actor_overlay, overwrite=overwrite)
    _write_text(debug_full_annotation, debug_text, overwrite=overwrite)

    unresolved = resolved.get("diagnostics", {}).get("unresolved_events", [])
    missing_reasons = parsed.get("diagnostics", {}).get("missing_reasons", [])
    if unresolved:
        print(f"WARNING: unresolved events: {len(unresolved)}")
    if missing_reasons:
        print(f"WARNING: tags missing reasons: {missing_reasons}")

    return {
        "annotated_for_jali": annotated_for_jali,
        "gaze_events_json": gaze_events_json,
        "actor_overlay_json": actor_overlay_json,
        "debug_full_annotation": debug_full_annotation,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LLM actor-style performance annotator.")
    parser.add_argument("--sequence-id", default=DEFAULT_SEQUENCE_ID)
    parser.add_argument("--candidate-jsonl", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--full-context-csv", type=Path, default=DEFAULT_FULL_CONTEXT)
    parser.add_argument("--full-context-window", type=int, default=2)
    parser.add_argument("--no-full-context", action="store_true")
    parser.add_argument("--prompt-template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--base-config", type=Path, default=DEFAULT_BASE_CONFIG)
    parser.add_argument("--jali-emotion-options", type=Path, default=DEFAULT_JALI_OPTIONS)
    parser.add_argument("--profile", choices=["mvp", "full_actor"], default="full_actor")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-prompt", type=Path, default=None)
    parser.add_argument("--output-context-pack", type=Path, default=None)
    parser.add_argument("--output-annotation", type=Path, default=None)
    parser.add_argument("--output-meta", type=Path, default=None)
    parser.add_argument("--words-jsonl", type=Path, default=None)
    parser.add_argument("--compile", action="store_true", help="Compile LLM output into JALI/gaze/actor overlay files.")
    parser.add_argument("--dry-run", action="store_true", help="Only build prompt/context; do not call OpenAI.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    candidate = find_candidate(args.candidate_jsonl, args.sequence_id)
    full_rows = []
    if not args.no_full_context:
        full_rows = load_full_context_window(
            args.full_context_csv,
            movie_id=str(candidate["movie_id"]),
            start_shot_idx=int(candidate["start_shot_idx"]),
            end_shot_idx=int(candidate["end_shot_idx"]),
            window=args.full_context_window,
        )

    context_pack = build_actor_context_pack(candidate, full_rows)
    profile = get_capability_profile(args.profile)
    template = load_prompt_template(args.prompt_template)
    extra_config = load_extra_config_texts(
        base_config=args.base_config,
        jali_emotion_options=args.jali_emotion_options,
    )
    prompt = build_actor_annotation_prompt(
        prompt_template=template,
        context_pack=context_pack,
        capability_profile=profile,
        extra_config=extra_config,
    )

    output_dir = args.output_dir
    output_prompt = args.output_prompt or output_dir / f"{args.sequence_id}__actor_prompt.txt"
    output_context = args.output_context_pack or output_dir / f"{args.sequence_id}__context_pack.json"
    output_annotation = args.output_annotation or output_dir / f"{args.sequence_id}__performance_annotation.txt"
    output_meta = args.output_meta or output_dir / f"{args.sequence_id}__llm_response_meta.json"

    _write_text(output_prompt, prompt, overwrite=args.overwrite)
    _write_json(output_context, context_pack, overwrite=args.overwrite)

    print(f"Prompt: {output_prompt}")
    print(f"Context pack: {output_context}")
    print(f"Profile: {args.profile}")
    print(f"Transcript chars: {len(context_pack.get('exact_transcript', ''))}")
    print(f"Full-context rows: {len(full_rows)}")

    if args.dry_run:
        print("Dry run: did not call OpenAI.")
        return

    llm_config = _load_llm_config(args.base_config)
    model = str(llm_config.get("model") or "gpt-5-mini")
    response = _call_openai(prompt, llm_config)
    annotation_text = _response_output_text(response)
    if not annotation_text.strip():
        raise RuntimeError("LLM response did not contain output text.")

    _write_text(output_annotation, annotation_text, overwrite=args.overwrite)
    _write_json(
        output_meta,
        _response_meta(response, model=model, prompt_path=output_prompt, output_path=output_annotation),
        overwrite=args.overwrite,
    )
    print(f"Annotation: {output_annotation}")
    print(f"LLM meta: {output_meta}")

    if args.compile:
        words_jsonl = args.words_jsonl or DEFAULT_WORDS_DIR / f"{args.sequence_id}__words.jsonl"
        outputs = _compile_outputs(
            annotation_path=output_annotation,
            words_jsonl=words_jsonl,
            clip_name=args.sequence_id,
            output_dir=output_dir,
            overwrite=args.overwrite,
        )
        for label, path in outputs.items():
            print(f"{label}: {path}")


if __name__ == "__main__":
    main()
