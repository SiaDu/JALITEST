from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CAPABILITY_PROFILES: dict[str, dict[str, Any]] = {
    "mvp": {
        "name": "mvp",
        "purpose": "Generate only tags that the current compiler/exporter can execute immediately.",
        "enabled_tags": {
            "gaze": "<g##=MODE-TARGET>...</g##>",
            "mask": "<m##=MaskName-Strength>...</m##>",
            "heart": "<h##=HeartName-Strength>...</h##>",
        },
        "disabled_tags": {
            "lid_state": "<l##=VALUE>...</l##>",
            "performative_blink": "<pb##=MODE-SUBTYPE>...</pb##>",
            "blink_suppression": "<bs##=SUPPRESS/ALLOW>...</bs##>",
        },
        "note": (
            "Do not output lid_state, performative_blink, or blink_suppression tags in [ANNOTATION]. "
            "You may discuss eyelids/blinks in [ANALYZE] only if it helps the acting strategy."
        ),
    },
    "full_actor": {
        "name": "full_actor",
        "purpose": "Generate full actor-style annotation for gaze, facial mask, eyelids, and intentional blinks.",
        "enabled_tags": {
            "gaze": "<g##=MODE-TARGET>...</g##>",
            "mask": "<m##=MaskName-Strength>...</m##>",
            "heart": "<h##=HeartName-Strength>...</h##>",
            "lid_state": "<l##=VALUE>...</l##>",
            "performative_blink": "<pb##=MODE-SUBTYPE>...</pb##>",
            "blink_suppression": "<bs##=SUPPRESS/ALLOW>...</bs##>",
        },
        "disabled_tags": {},
        "note": (
            "Use lid_state and blink tags sparingly. They must describe actor choices, "
            "not ordinary physiological blink behavior."
        ),
    },
}


PROMPT_CONTEXT_EXCLUDED_KEYS = {
    "exact_transcript",
    "scene_targets",
    "target_context",
}


CAPABILITY_PROFILE_INCLUDED_KEYS = {
    "name",
    "purpose",
    "enabled_tags",
    "disabled_tags",
    "note",
}


def load_prompt_template(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def get_capability_profile(profile_name: str) -> dict[str, Any]:
    if profile_name not in CAPABILITY_PROFILES:
        raise ValueError(
            f"Unknown capability profile: {profile_name}. "
            f"Available: {sorted(CAPABILITY_PROFILES)}"
        )
    return CAPABILITY_PROFILES[profile_name]


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


def _compact_capability_profile(capability_profile: dict[str, Any]) -> dict[str, Any]:
    return {
        key: capability_profile[key]
        for key in CAPABILITY_PROFILE_INCLUDED_KEYS
        if key in capability_profile
    }


def build_actor_annotation_prompt(
    *,
    prompt_template: str,
    context_pack: dict[str, Any],
    capability_profile: dict[str, Any],
    transcript: str | None = None,
    extra_config: dict[str, Any] | None = None,
) -> str:
    """Inject compact context, capability profile, and exact transcript."""
    exact_transcript = transcript or context_pack.get("exact_transcript", "")
    if not str(exact_transcript).strip():
        raise ValueError("No exact transcript provided.")

    replacements = {
        "{{context_pack}}": json.dumps(_compact_prompt_context(context_pack), ensure_ascii=False, indent=2),
        "{{capability_profile}}": json.dumps(_compact_capability_profile(capability_profile), ensure_ascii=False, indent=2),
        "{{transcript}}": str(exact_transcript),
        "{{extra_config}}": "",
    }

    prompt = prompt_template
    for token, value in replacements.items():
        prompt = prompt.replace(token, value)
    return prompt
