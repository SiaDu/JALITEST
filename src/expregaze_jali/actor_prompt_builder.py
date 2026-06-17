from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROMPT_CONTEXT_EXCLUDED_KEYS = {
    "exact_transcript",
    "scene_targets",
    "target_context",
}

def load_prompt_template(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _read_optional_text(path: str | Path | None, *, max_chars: int = 8000) -> str:
    if path is None:
        return ""
    file_path = Path(path)
    if not file_path.exists():
        return ""
    text = file_path.read_text(encoding="utf-8")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n...[truncated]"


def load_extra_config_texts(
    *,
    base_config: str | Path | None = None,
    jali_emotion_options: str | Path | None = None,
) -> dict[str, Any]:
    """
    Backward-compatible loader.

    The compact actor prompt no longer injects raw config text, because it adds
    noise and encourages the model to copy irrelevant options. Keep this helper
    so older callers do not break.
    """
    return {
        "base_config_path": str(base_config) if base_config else None,
        "base_config_text": _read_optional_text(base_config),
        "jali_emotion_options_path": str(jali_emotion_options) if jali_emotion_options else None,
        "jali_emotion_options_text": _read_optional_text(jali_emotion_options),
    }


def _compact_prompt_context(context_pack: dict[str, Any]) -> dict[str, Any]:
    """Return only context useful to the LLM.

    Keep target metadata in context_pack.json for compiler/debug use, but do not
    inject it into the actor prompt. The LLM can infer targets from story/action/
    transcript context; scene_targets/target_context are keyword hints and tend to
    add noise.
    """
    out: dict[str, Any] = {}
    for key, value in context_pack.items():
        if key in PROMPT_CONTEXT_EXCLUDED_KEYS:
            continue
        if value in (None, "", [], {}):
            continue
        out[key] = value
    return out

def build_actor_annotation_prompt(
    *,
    prompt_template: str,
    context_pack: dict[str, Any],
    transcript: str | None = None,
    extra_config: dict[str, Any] | None = None,
) -> str:
    """Inject compact context, capability profile, and exact transcript."""
    exact_transcript = transcript or context_pack.get("exact_transcript", "")
    if not str(exact_transcript).strip():
        raise ValueError("No exact transcript provided.")

    replacements = {
        "{{context_pack}}": json.dumps(_compact_prompt_context(context_pack), ensure_ascii=False, indent=2),
        "{{transcript}}": str(exact_transcript),
        "{{extra_config}}": "",
    }

    prompt = prompt_template
    for token, value in replacements.items():
        prompt = prompt.replace(token, value)
    return prompt
