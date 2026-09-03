"""Prompt-template loading shared by current plan generators."""

from __future__ import annotations

from pathlib import Path


def load_prompt_template(path: str | Path) -> str:
    """Read a required prompt template as UTF-8 text."""
    return Path(path).read_text(encoding="utf-8")
