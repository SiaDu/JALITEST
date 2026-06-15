from __future__ import annotations

from pathlib import Path

from expregaze.data.textgrid_parser import parse_textgrid_words as _parse_textgrid_words


def parse_textgrid_words(textgrid_path: str | Path) -> list[dict]:
    """Return word intervals from the TextGrid `words` tier."""
    return _parse_textgrid_words(textgrid_path, tier_name="words")

