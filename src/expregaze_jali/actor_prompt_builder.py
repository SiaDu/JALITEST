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


def _read_optional_text(path: str | Path | None, *, max_chars: int = 12000) -> str:
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
    jali_emotion_options: str | Path | None = None,
    performance_rules: str | Path | None = None,
) -> dict[str, Any]:
    """Load prompt-only config text for the actor annotator.

    Important: this intentionally does not read configs/base.yaml. base.yaml is a
    runtime config for Step 01 LLM API settings. The prompt only receives acting /
    annotation constraints and JALI mask/heart options.
    """
    return {
        "jali_emotion_options": {
            "path": str(jali_emotion_options) if jali_emotion_options else None,
            "text": _read_optional_text(jali_emotion_options),
        },
        "performance_rules": {
            "path": str(performance_rules) if performance_rules else None,
            "text": _read_optional_text(performance_rules),
        },
    }


def _compact_prompt_context(context_pack: dict[str, Any]) -> dict[str, Any]:
    """Return only context useful to the actor annotator prompt.

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


def _compact_extra_config(extra_config: dict[str, Any] | None) -> dict[str, Any]:
    if not extra_config:
        return {}

    out: dict[str, Any] = {}
    for key, value in extra_config.items():
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
    """Inject exact transcript, compact scene context, and prompt-only configs."""
    exact_transcript = transcript or context_pack.get("exact_transcript", "")
    if not str(exact_transcript).strip():
        raise ValueError("No exact transcript provided.")

    replacements = {
        "{{context_pack}}": json.dumps(_compact_prompt_context(context_pack), ensure_ascii=False, indent=2),
        "{{extra_config}}": json.dumps(_compact_extra_config(extra_config), ensure_ascii=False, indent=2),
        "{{transcript}}": str(exact_transcript),
    }

    prompt = prompt_template
    for token, value in replacements.items():
        prompt = prompt.replace(token, value)
    return prompt
