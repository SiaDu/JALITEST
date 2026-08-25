"""Deterministically compile a dual plan onto two scene-global speaker alignments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from expregaze_jali.compile_performance_plan import TimingAlignment, _validate_words
from expregaze_jali.performance_event_resolver import load_words_jsonl
from expregaze_jali.text_utils import normalize_word
from expregaze_jali.textgrid_parser import parse_textgrid_words
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model


def discover_character_timing(audio_folder: str | Path, sound_file: str) -> TimingAlignment:
    folder, stem = Path(audio_folder), Path(str(sound_file)).name
    if not folder.is_dir(): raise FileNotFoundError(f"Input Audio Folder does not exist: {folder}")
    candidates = sorted({*folder.glob(f"{stem}*words*.jsonl"), *folder.glob(f"{stem}.TextGrid"), *folder.glob(f"{stem}.textgrid")})
    if not candidates: raise FileNotFoundError(f"No timing alignment found for runtime clip {stem!r}.")
    if len(candidates) != 1: raise ValueError(f"Ambiguous timing alignments for runtime clip {stem!r}: " + ", ".join(map(str, candidates)))
    path = candidates[0]
    words = load_words_jsonl(path) if path.suffix.lower() == ".jsonl" else parse_textgrid_words(path)
    _validate_words(words, path)
    return TimingAlignment(path.resolve(), "words_jsonl" if path.suffix.lower() == ".jsonl" else "textgrid", words)


def compile_dual_performance_plan(*, performance_plan_path: str | Path, script: str, audio_folder: str | Path, fps: float, runtime_mapping: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    plan = json.loads(Path(performance_plan_path).read_text(encoding="utf-8"))
    if plan.get("schema_version") != "dual_performance_plan_v0": raise ValueError("Expected dual_performance_plan_v0.")
    mapping = {alias: runtime_mapping.get(alias) for alias in ("A", "B")}
    if not all(isinstance(row, dict) and row.get("script_name") and row.get("sound_file") for row in mapping.values()): raise ValueError("Runtime mapping requires A/B script_name and sound_file.")
    model = build_conversation_anchor_model(script, character_a=str(mapping["A"]["script_name"]), character_b=str(mapping["B"]["script_name"]))
    timings = {alias: discover_character_timing(audio_folder, str(row["sound_file"])) for alias, row in mapping.items()}
    cursors = {"A": 0, "B": 0}; anchor_times: dict[str, dict[str, Any]] = {}
    for turn in model.turns:
        alias = next(key for key, name in model.aliases.items() if name == turn.speaker)
        timing = timings[alias]
        for anchor in turn.anchors:
            index = cursors[alias]
            if index >= len(timing.words): raise ValueError(f"{alias} {turn.turn_id}: missing timing word for {anchor.text!r} in {timing.path}")
            word = timing.words[index]; actual = str(word.get("norm") or word.get("word") or "")
            if normalize_word(anchor.text) != normalize_word(actual): raise ValueError(f"{alias} {turn.turn_id}: expected word {anchor.text!r}, timing word {actual!r}, source {timing.path}")
            anchor_times[anchor.anchor_id] = {"speaker": turn.speaker, "text": anchor.text, "start": float(word["start"]), "end": float(word["end"]), "timing_source": str(timing.path)}
            cursors[alias] += 1
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    timing_path = out / "conversation_anchor_timing.json"; timing_path.write_text(json.dumps(anchor_times, indent=2)+"\n", encoding="utf-8")
    artifacts: dict[str, str] = {"conversation_anchor_timing": str(timing_path)}
    phrases = plan.get("phrases", [])
    anchor_at_start = {anchor.char_start: anchor.anchor_id for anchor in model.anchors}
    for alias in ("A", "B"):
        events=[]
        for i, phrase in enumerate(phrases):
            start_id=anchor_at_start.get(int(phrase["span"]["char_start"]))
            if not start_id: raise ValueError(f"Phrase {phrase.get('phrase_id')} does not begin at a conversation anchor.")
            start=anchor_times[start_id]["start"]
            next_phrase=phrases[i+1] if i+1<len(phrases) else None
            if next_phrase and next_phrase.get("span", {}).get("turn_id")==phrase.get("span", {}).get("turn_id"):
                next_id=anchor_at_start[int(next_phrase["span"]["char_start"])]; end=anchor_times[next_id]["start"]
            else:
                turn=next(t for t in model.turns if t.turn_id==phrase["span"]["turn_id"]); end=anchor_times[turn.anchors[-1].anchor_id]["end"]
            for channel, value in phrase["states"][alias].items():
                if value not in (None, "NONE"):
                    events.append({"phrase_id": phrase.get("source_proposal_id"), "speaker": phrase.get("speaker"), "actor": alias, "intent": phrase.get("intent"), "channel": channel, "value": value, "source_char_span": phrase.get("span"), "resolved_time": {"start": start, "end": end}, "reason": ((phrase.get("rationale") or {}).get(alias, {}) or {}).get(channel)})
        path=out/"characters"/alias/"semantic_events_resolved.json"; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps({"events":events},indent=2)+"\n",encoding="utf-8"); artifacts[alias]=str(path)
    manifest={"schema_version":"dual_animation_manifest_v0","full_script_source":str(performance_plan_path),"fps":float(fps),"character_runtime_mapping":runtime_mapping,"artifacts":artifacts,"warnings":[]}
    path=out/"dual_animation_manifest.json"; path.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8"); manifest["manifest_path"]=str(path); return manifest


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--performance-plan",type=Path,required=True); p.add_argument("--script-file",type=Path,required=True); p.add_argument("--audio-folder",type=Path,required=True); p.add_argument("--fps",type=float,required=True); p.add_argument("--runtime-mapping",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); a=p.parse_args(); print(json.dumps(compile_dual_performance_plan(performance_plan_path=a.performance_plan,script=a.script_file.read_text(encoding="utf-8"),audio_folder=a.audio_folder,fps=a.fps,runtime_mapping=json.loads(a.runtime_mapping.read_text(encoding="utf-8")),output_dir=a.output_dir),indent=2))
if __name__ == "__main__": main()
