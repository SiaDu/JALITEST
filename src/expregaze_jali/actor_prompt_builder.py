from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROMPT_CONTEXT_EXCLUDED_KEYS = {
    "exact_transcript",
    "scene_targets",
    "target_context",
}

DEFAULT_EXTRA_CONFIG_FILES = (
    Path("configs/jali_emotion_options.yaml"),
    Path("configs/performance_rules.yaml"),
)


def load_prompt_template(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def _read_optional_text(path: str | Path, *, max_chars: int = 20000) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    text = file_path.read_text(encoding="utf-8")
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n...[truncated]"


def load_extra_config_texts(
    paths: list[str | Path] | tuple[str | Path, ...] | None = None,
    *,
    max_chars_per_file: int = 20000,
) -> dict[str, Any]:
    """Load prompt-only config snippets for the actor annotator.

    Important: this intentionally does NOT read configs/base.yaml. base.yaml is
    runtime configuration for Step 01 OpenAI calls, not prompt context.
    """
    config_paths = list(paths or DEFAULT_EXTRA_CONFIG_FILES)
    sources: list[dict[str, Any]] = []
    for config_path in config_paths:
        path = Path(config_path)
        sources.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "text": _read_optional_text(path, max_chars=max_chars_per_file),
            }
        )
    return {
        "note": "Prompt-only configs. configs/base.yaml is intentionally excluded; it is runtime LLM config.",
        "sources": sources,
    }


def _compact_prompt_context(context_pack: dict[str, Any]) -> dict[str, Any]:
    """Return only context useful to the actor annotator prompt.

    exact_transcript is injected separately. scene_targets/target_context are kept
    out of the prompt if they exist, because they tend to add target-vocabulary
    noise; the model should infer targets from scene/story/script context.
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
    """Inject compact scene context, extra config, and exact transcript."""
    exact_transcript = transcript or context_pack.get("exact_transcript", "")
    if not str(exact_transcript).strip():
        raise ValueError("No exact transcript provided.")

    replacements = {
        "{{context_pack}}": json.dumps(_compact_prompt_context(context_pack), ensure_ascii=False, indent=2),
        "{{extra_config}}": json.dumps(extra_config or {}, ensure_ascii=False, indent=2),
        "{{transcript}}": str(exact_transcript),
    }

    prompt = prompt_template
    for token, value in replacements.items():
        prompt = prompt.replace(token, value)
    return prompt
