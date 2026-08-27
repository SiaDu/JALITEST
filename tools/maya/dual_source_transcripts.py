"""Deterministic clean JALI source transcript export for dual dialogue."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
from typing import Iterable


def resolve_character_wav(audio_folder: str | Path, character: str) -> Path:
    folder, name = Path(audio_folder), str(character).strip()
    if not folder.is_dir():
        raise ValueError("Input Audio Folder does not exist.")
    candidates = [path for path in folder.glob("*.wav") if path.stem.casefold() == name.casefold()]
    if not candidates:
        candidates = [path for path in folder.glob("*.wav") if path.stem.casefold().endswith("_" + name.casefold())]
    if len(candidates) != 1:
        label = "No WAV" if not candidates else "Ambiguous WAVs"
        rendered = ", ".join(str(path) for path in candidates) or "none"
        raise ValueError(f"{label} for {name!r}: {rendered}")
    return candidates[0]


def extract_speaker_dialogue(script: str, characters: Iterable[str]) -> dict[str, list[str]]:
    names = [str(name).strip() for name in characters]
    if len(names) != 2 or not all(names) or names[0].casefold() == names[1].casefold():
        raise ValueError("Dual source transcript export requires two distinct Script Character names.")
    result = {name: [] for name in names}
    by_folded = {name.casefold(): name for name in names}
    matcher = re.compile(r"^\s*([^:\n]+):\s*(.*?)\s*$")
    for line in str(script).splitlines():
        match = matcher.match(line)
        if not match:
            if line.strip():
                raise ValueError(f"Every dual dialogue line must begin with a Script Character label: {line!r}")
            continue
        actor = by_folded.get(match.group(1).strip().casefold())
        if actor is None:
            raise ValueError(f"Unknown speaker label in Input Script: {match.group(1)!r}")
        text = match.group(2)
        if text:
            result[actor].append(text)
    return result


def _atomic_text(path: Path, text: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") != text:
        backup = path.with_suffix(path.suffix + ".jalitest-backup")
        if not backup.exists(): shutil.copy2(path, backup)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def export_dual_source_transcripts(*, script: str, audio_folder: str | Path, characters: Iterable[str]) -> dict[str, dict[str, object]]:
    names = list(characters)
    dialogue = extract_speaker_dialogue(script, names)
    result: dict[str, dict[str, object]] = {}
    for name in names:
        wav = resolve_character_wav(audio_folder, name)
        txt = wav.with_suffix(".txt")
        text = "\n".join(dialogue[name]) + ("\n" if dialogue[name] else "")
        _atomic_text(txt, text)
        result[name] = {"wav": str(wav), "txt": str(txt), "utterances": len(dialogue[name])}
    return result
