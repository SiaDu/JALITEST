from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

try:
    from openai import APIConnectionError, OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]
    APIConnectionError = None  # type: ignore[assignment]

DEFAULT_SEQUENCE_ID = "Jali_proto_candidate_001_ProfessorCrystal"
DEFAULT_BASE_CONFIG = Path("configs/base.yaml")
DEFAULT_LLM_PROCESS_DIR = Path("data/processed/gaze_script/llm_process")


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
    return max(int(value), 1)


def _coerce_positive_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    return max(float(value), 0.0)


def _supports_temperature(model: str) -> bool:
    return not model.lower().startswith("gpt-5")


def _build_openai_request(prompt: str, llm_config: dict[str, Any]) -> dict[str, Any]:
    model = str(llm_config.get("model") or "gpt-5-mini")
    temperature = _coerce_temperature(llm_config.get("temperature"))
    max_output_tokens = _coerce_max_output_tokens(llm_config.get("max_output_tokens"))
    reasoning_effort = llm_config.get("reasoning_effort")

    request: dict[str, Any] = {"model": model, "input": prompt}

    if temperature is not None and _supports_temperature(model):
        request["temperature"] = temperature
    elif temperature is not None:
        print(f"Skipping unsupported temperature parameter for model {model!r}.")

    if max_output_tokens is not None:
        request["max_output_tokens"] = max_output_tokens

    if reasoning_effort:
        request["reasoning"] = {"effort": str(reasoning_effort)}

    return request


def _response_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def _response_meta(response: Any, *, model: str, prompt_path: Path, output_path: Path) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    incomplete_details = getattr(response, "incomplete_details", None)
    return {
        "response_id": getattr(response, "id", None),
        "model": model,
        "prompt_path": str(prompt_path),
        "annotation_path": str(output_path),
        "usage": usage.model_dump() if hasattr(usage, "model_dump") else usage,
        "status": getattr(response, "status", None),
        "incomplete_details": incomplete_details.model_dump() if hasattr(incomplete_details, "model_dump") else incomplete_details,
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
        "Then rerun scripts/02_run_actor_llm.sh from the same shell."
    )
    raise RuntimeError(proxy_hint) from last_exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Step 02: call OpenAI once to generate performance annotation. Does not compile.")
    parser.add_argument("--sequence-id", default=DEFAULT_SEQUENCE_ID)
    parser.add_argument("--base-config", type=Path, default=DEFAULT_BASE_CONFIG)
    parser.add_argument("--llm-process-dir", type=Path, default=DEFAULT_LLM_PROCESS_DIR)
    parser.add_argument("--prompt-path", type=Path, default=None)
    parser.add_argument("--output-annotation", type=Path, default=None)
    parser.add_argument("--output-meta", type=Path, default=None)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    clip = args.sequence_id
    prompt_path = args.prompt_path or args.llm_process_dir / f"{clip}__actor_prompt.txt"
    output_annotation = args.output_annotation or args.llm_process_dir / f"{clip}__performance_annotation.txt"
    output_meta = args.output_meta or args.llm_process_dir / f"{clip}__llm_response_meta.json"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}. Run scripts/01_build_actor_prompt.sh first.")

    prompt = prompt_path.read_text(encoding="utf-8")
    llm_config = _load_llm_config(args.base_config)
    model = str(llm_config.get("model") or "gpt-5-mini")

    print(f"Prompt: {prompt_path}")
    print(f"Annotation output: {output_annotation}")
    print("LLM calls: 1")

    response = _call_openai(prompt, llm_config)
    _write_json(
        output_meta,
        _response_meta(response, model=model, prompt_path=prompt_path, output_path=output_annotation),
        overwrite=args.overwrite,
    )

    status = getattr(response, "status", None)
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) if details is not None else None
        raise RuntimeError(
            f"LLM response incomplete. reason={reason!r}. "
            f"Increase llm.max_output_tokens and/or lower llm.reasoning_effort. Meta saved to: {output_meta}"
        )

    annotation_text = _response_output_text(response)
    if not annotation_text.strip():
        raise RuntimeError("LLM response did not contain output text.")

    missing_sections = [
        section for section in ("[ANALYZE]", "[ANNOTATION]", "[REASONS]")
        if section not in annotation_text
    ]
    if missing_sections:
        raise RuntimeError(
            f"LLM response missing required sections: {missing_sections}. "
            f"Not writing partial annotation. Meta saved to: {output_meta}"
        )

    _write_text(output_annotation, annotation_text, overwrite=args.overwrite)
    print(f"Annotation: {output_annotation}")
    print(f"LLM meta: {output_meta}")


if __name__ == "__main__":
    main()
