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


def build_actor_annotation_prompt(
    *,
    prompt_template: str,
    context_pack: dict[str, Any],
    transcript: str | None = None,
) -> str:
    """Inject compact scene context and exact transcript into the prompt."""
    exact_transcript = transcript or context_pack.get("exact_transcript", "")
    if not str(exact_transcript).strip():
        raise ValueError("No exact transcript provided.")

    replacements = {
        "{{context_pack}}": json.dumps(_compact_prompt_context(context_pack), ensure_ascii=False, indent=2),
        "{{transcript}}": str(exact_transcript),
    }

    prompt = prompt_template
    for token, value in replacements.items():
        prompt = prompt.replace(token, value)
    return prompt
