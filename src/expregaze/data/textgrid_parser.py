from __future__ import annotations

import argparse
import csv
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml


_NON_WORD_MARKERS = {
    "",
    "<eps>",
    "eps",
    "sil",
    "sp",
    "spn",
    "xxnonverbalxx",
}


_QUOTE_AND_PUNCT_TRANSLATION = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "–": "-",
        "—": "-",
        "…": "...",
    }
)


def _read_text_file(path: str | Path) -> str:
    data = Path(path).read_bytes()

    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace")


def _decode_praat_string(value: str) -> str:
    # Praat escapes quotes inside strings as doubled quotes.
    return value.replace('""', '"').strip()


def _normalize_word(word: str) -> str:
    text = unicodedata.normalize("NFKC", word)
    text = text.translate(_QUOTE_AND_PUNCT_TRANSLATION)
    text = text.strip()

    # Remove surrounding quotation marks.
    text = text.strip("\"'")

    # Lowercase for stable matching.
    text = text.lower()

    # Remove leading/trailing punctuation, but keep internal apostrophes:
    # "that's" stays "that's"; "quality," becomes "quality".
    text = re.sub(r"^[^\w]+|[^\w]+$", "", text)

    return text


def _resolve_path(path_value: str | Path | None, base_dir: Path) -> Path | None:
    if path_value is None:
        return None
    path = Path(path_value)
    return path if path.is_absolute() else base_dir / path


def _resolve_existing_path(path_value: str | Path, base_dir: Path) -> Path:
    path = _resolve_path(path_value, base_dir)
    assert path is not None
    if path.exists():
        return path

    # Allow configs to store Windows paths while the parser runs under WSL.
    text = str(path_value)
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if os.name != "nt" and match:
        wsl_path = Path("/mnt") / match.group(1).lower() / match.group(2).replace("\\", "/")
        if wsl_path.exists():
            return wsl_path

    return path


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _textgrid_tier_blocks(text: str) -> list[tuple[str, str]]:
    item_pattern = re.compile(
        r"(?:^|\n)\s*item\s*\[\d+\]:\s*(.*?)(?=\n\s*item\s*\[\d+\]:|\Z)",
        re.DOTALL,
    )

    blocks: list[tuple[str, str]] = []
    for item_match in item_pattern.finditer(text):
        tier_block = item_match.group(1)
        name_match = re.search(r'name\s*=\s*"([^"]*)"', tier_block)
        if name_match:
            blocks.append((name_match.group(1), tier_block))
    return blocks


def _parse_textgrid_words_with_stats(textgrid_path: str | Path, tier_name: str = "words") -> tuple[list[dict], dict[str, Any]]:
    text = _read_text_file(textgrid_path)
    wanted_tier = tier_name.lower()
    tier_blocks = _textgrid_tier_blocks(text)

    interval_pattern = re.compile(
        r"intervals\s*\[\d+\]:\s*"
        r"\n\s*xmin\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*"
        r"\n\s*xmax\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*"
        r'\n\s*text\s*=\s*"((?:""|[^"])*)"',
        re.DOTALL,
    )

    for current_tier_name, tier_block in tier_blocks:
        if current_tier_name.lower() != wanted_tier:
            continue

        words: list[dict] = []
        total_intervals = 0
        skipped_intervals = 0

        for interval_match in interval_pattern.finditer(tier_block):
            total_intervals += 1
            start = float(interval_match.group(1))
            end = float(interval_match.group(2))
            word = _decode_praat_string(interval_match.group(3))
            norm = _normalize_word(word)

            if word.strip().lower() in _NON_WORD_MARKERS or norm in _NON_WORD_MARKERS:
                skipped_intervals += 1
                continue

            words.append(
                {
                    "word": word,
                    "norm": norm,
                    "start": start,
                    "end": end,
                }
            )

        stats = {
            "tier_name": current_tier_name,
            "available_tiers": [name for name, _ in tier_blocks],
            "total_interval_count": total_intervals,
            "skipped_interval_count": skipped_intervals,
            "word_count": len(words),
        }
        return words, stats

    raise ValueError(
        f"No `{tier_name}` tier found in TextGrid: {textgrid_path}. "
        f"Available tiers: {[name for name, _ in tier_blocks]}"
    )


def parse_textgrid_words(textgrid_path: str | Path, tier_name: str = "words") -> list[dict]:
    """
    Return word intervals from a Praat TextGrid tier.

    Example:
    [
        {
            "word": "Quality",
            "norm": "quality",
            "start": 0.42,
            "end": 0.81,
        }
    ]

    `word` preserves the original TextGrid text.
    `norm` is a normalized form for matching/alignment.
    """
    words, _stats = _parse_textgrid_words_with_stats(textgrid_path, tier_name=tier_name)
    return words


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["word", "norm", "start", "end"])
        writer.writeheader()
        writer.writerows(rows)


def _values_from_paths_config(paths_config_path: Path) -> dict[str, Any]:
    config = _load_yaml(paths_config_path)
    project_root_value = config.get("project", {}).get("root", ".")
    project_root = _resolve_path(project_root_value, paths_config_path.parent.parent)
    assert project_root is not None

    jali_config = config.get("jali", {})
    textgrid_config = config.get("textgrid", {})
    clip_name = str(jali_config.get("clip_name", "textgrid")).strip() or "textgrid"
    jali_project_root = _resolve_existing_path(jali_config.get("project_root", project_root), project_root)

    output_dir = _resolve_path(textgrid_config.get("output_dir", "data/processed/textgrid"), project_root)
    assert output_dir is not None

    return {
        "project_root": project_root,
        "jali_project_root": jali_project_root,
        "clip_name": clip_name,
        "textgrid_file": _resolve_existing_path(jali_config.get("textgrid_file"), jali_project_root),
        "tier_name": str(textgrid_config.get("tier_name", "words")),
        "words_jsonl": _resolve_path(
            textgrid_config.get("words_jsonl", f"data/processed/textgrid/{clip_name}__words.jsonl"),
            project_root,
        ),
        "words_csv": _resolve_path(
            textgrid_config.get("words_csv", f"data/processed/textgrid/{clip_name}__words.csv"),
            project_root,
        ),
        "summary_json": _resolve_path(
            textgrid_config.get("summary_json", f"data/processed/textgrid/{clip_name}__textgrid_summary.json"),
            project_root,
        ),
        "output_dir": output_dir,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse JALI/Praat TextGrid word intervals.")
    parser.add_argument("--paths-config", type=Path, default=Path("configs/path_local.yaml"))
    parser.add_argument("--textgrid-file", type=Path, default=None)
    parser.add_argument("--tier-name", type=str, default=None)
    parser.add_argument("--words-jsonl", type=Path, default=None)
    parser.add_argument("--words-csv", type=Path, default=None)
    parser.add_argument("--summary-json", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    values = _values_from_paths_config(args.paths_config)
    project_root = Path(values["project_root"])

    textgrid_file = args.textgrid_file or values["textgrid_file"]
    tier_name = args.tier_name or values["tier_name"]
    words_jsonl = args.words_jsonl or values["words_jsonl"]
    words_csv = args.words_csv or values["words_csv"]
    summary_json = args.summary_json or values["summary_json"]

    textgrid_file = _resolve_existing_path(textgrid_file, project_root)
    words_jsonl = _resolve_path(words_jsonl, project_root)
    words_csv = _resolve_path(words_csv, project_root)
    summary_json = _resolve_path(summary_json, project_root)
    assert words_jsonl is not None and words_csv is not None and summary_json is not None

    words, stats = _parse_textgrid_words_with_stats(textgrid_file, tier_name=tier_name)
    _write_jsonl(words_jsonl, words)
    _write_csv(words_csv, words)

    summary = {
        "clip_name": values["clip_name"],
        "textgrid_file": str(textgrid_file),
        "tier_name": stats["tier_name"],
        "available_tiers": stats["available_tiers"],
        "total_interval_count": stats["total_interval_count"],
        "skipped_interval_count": stats["skipped_interval_count"],
        "word_count": stats["word_count"],
        "words_jsonl": str(words_jsonl),
        "words_csv": str(words_csv),
    }
    _write_json(summary_json, summary)

    print(f"TextGrid: {textgrid_file}")
    print(f"Tier: {summary['tier_name']}")
    print(f"Words: {summary['word_count']}")
    print(f"Skipped intervals: {summary['skipped_interval_count']}")
    print(f"JSONL: {words_jsonl}")
    print(f"CSV: {words_csv}")
    print(f"Summary: {summary_json}")


if __name__ == "__main__":
    main()
