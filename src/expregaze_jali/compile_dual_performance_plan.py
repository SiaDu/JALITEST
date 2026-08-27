"""Deterministically compile a dual plan onto two scene-global speaker alignments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
import wave
import re

from expregaze_jali.compile_performance_plan import TimingAlignment, _validate_words
from expregaze_jali.performance_event_resolver import load_words_jsonl
from expregaze_jali.text_utils import normalize_word
from expregaze_jali.textgrid_parser import parse_textgrid_words
from expregaze_jali.jali_annotation_exporter import build_dual_speaker_jali_annotation
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model, speaker_key
from expregaze_jali.dual_performance_plan_from_proposal import adapt_dual_performance_plan_v0


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


def _wav_duration(folder: str | Path, sound_file: str) -> tuple[Path, float]:
    path = Path(folder) / f"{Path(str(sound_file)).name}.wav"
    if not path.is_file(): raise FileNotFoundError(f"Runtime WAV not found: {path}")
    with wave.open(str(path), "rb") as audio:
        return path, audio.getnframes() / float(audio.getframerate())


def _validate_plan(plan: dict[str, Any], model: Any) -> list[dict[str, Any]]:
    if plan.get("schema_version") != "dual_performance_plan_v1": raise ValueError("Expected dual_performance_plan_v1.")
    if not isinstance(plan.get("characters"), list) or len(plan["characters"]) != 2: raise ValueError("Dual plan requires two ordered character names.")
    characters = plan["characters"]
    phrases = plan.get("phrases")
    if not isinstance(phrases, list): raise ValueError("Dual plan requires a phrases list.")
    turns={turn.turn_id for turn in model.turns}
    for index, phrase in enumerate(phrases, 1):
        if not isinstance(phrase,dict) or not phrase.get("phrase_id") or not isinstance(phrase.get("span"),dict) or phrase["span"].get("turn_id") not in turns or not isinstance(phrase.get("states"),dict) or not all(isinstance(phrase["states"].get(name),dict) for name in characters) or phrase.get("speaker") not in characters:
            raise ValueError(f"Dual plan phrase {index} requires phrase_id, known span turn_id, named states, and a named speaker.")
    return phrases


def build_canonical_phrase_timeline(
    phrases: list[dict[str, Any]], model: Any, anchor_times: dict[str, dict[str, Any]], *, epsilon: float = 1e-6
) -> list[dict[str, Any]]:
    """Derive one script-ordered, non-overlapping timeline for both actors.

    Per-speaker TextGrids are evidence, not the ordering authority: a word on
    a silence-padded isolated track may otherwise precede the previous script
    line.  This helper never reorders plan phrases by their raw timestamps.
    """
    anchor_at_start = {anchor.char_start: anchor.anchor_id for anchor in model.anchors}
    turns = {turn.turn_id: turn for turn in model.turns}
    raw: list[dict[str, Any]] = []
    for index, phrase in enumerate(phrases):
        span = phrase["span"]
        phrase_id = str(phrase["phrase_id"])
        start_id = anchor_at_start.get(int(span["char_start"]))
        if not start_id:
            raise ValueError(f"Phrase {phrase_id} does not begin at a conversation anchor.")
        next_phrase = phrases[index + 1] if index + 1 < len(phrases) else None
        if next_phrase and next_phrase["span"].get("turn_id") == span.get("turn_id"):
            next_id = anchor_at_start.get(int(next_phrase["span"]["char_start"]))
            if not next_id:
                raise ValueError(f"Phrase {next_phrase.get('phrase_id')} does not begin at a conversation anchor.")
            raw_end = float(anchor_times[next_id]["start"])
        else:
            turn = turns.get(span["turn_id"])
            if turn is None:
                raise ValueError(f"Phrase {phrase_id} has unknown turn {span['turn_id']!r}.")
            raw_end = float(anchor_times[turn.anchors[-1].anchor_id]["end"])
        raw_start = float(anchor_times[start_id]["start"])
        if raw_end + epsilon < raw_start:
            raise ValueError(f"Phrase {phrase_id} has invalid raw timing: end precedes start.")
        raw.append({"phrase": phrase, "phrase_id": phrase_id, "plan_index": index, "speaker": phrase.get("speaker"), "raw_start": raw_start, "raw_source_end": raw_end})

    timeline: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        previous = timeline[-1] if timeline else None
        canonical_start = item["raw_start"]
        adjusted = False
        if previous is not None:
            boundary = max(float(previous["raw_source_end"]), float(previous["canonical_start"]))
            if canonical_start + epsilon < boundary:
                canonical_start = boundary
                adjusted = True
        timeline.append({**item, "canonical_start": canonical_start, "start_adjusted": adjusted, **({"adjustment_reason": "raw_start preceded previous phrase source end"} if adjusted else {})})

    for index, item in enumerate(timeline):
        if index + 1 < len(timeline):
            item["canonical_end"] = float(timeline[index + 1]["canonical_start"])
        else:
            item["canonical_end"] = max(float(item["canonical_start"]), float(item["raw_source_end"]))
        if item["canonical_end"] <= item["canonical_start"] + epsilon:
            raise ValueError(
                f"Canonical timing collapsed phrase {item['phrase_id']} to zero duration after alignment repair."
            )
        if item["start_adjusted"] and item["canonical_start"] > item["raw_source_end"] + epsilon:
            raise ValueError(
                f"Canonical timing places phrase {item['phrase_id']} after its own source end after alignment repair."
            )
    for index, item in enumerate(timeline[1:], start=1):
        previous = timeline[index - 1]
        if item["canonical_start"] + epsilon < previous["canonical_start"] or previous["canonical_end"] != item["canonical_start"]:
            raise ValueError("Could not build a monotonic canonical phrase timeline.")
    return timeline


def compile_dual_performance_plan(*, performance_plan_path: str | Path, script: str, audio_folder: str | Path, fps: float, runtime_mapping: dict[str, Any], output_dir: str | Path, script_source: str | Path | None = None) -> dict[str, Any]:
    raw_plan = json.loads(Path(performance_plan_path).read_text(encoding="utf-8"))
    plan = adapt_dual_performance_plan_v0(raw_plan)
    characters = plan.get("characters")
    if not isinstance(characters, list) or len(characters) != 2:
        raise ValueError("Dual plan requires two ordered character names.")
    if raw_plan.get("schema_version") == "dual_performance_plan_v0":
        aliases = raw_plan.get("characters", {})
        runtime_mapping = {
            aliases[alias]: value for alias, value in runtime_mapping.items()
            if alias in aliases
        }
    mapping = {name: runtime_mapping.get(name) for name in characters}
    if not all(isinstance(row, dict) and row.get("script_name") and row.get("sound_file") for row in mapping.values()):
        raise ValueError("Runtime mapping requires one named script_name and sound_file entry per plan character.")
    for name in characters:
        if speaker_key(name) != speaker_key(str(mapping[name]["script_name"])):
            raise ValueError(f"Runtime mapping {name} script_name does not match dual plan character.")
    model = build_conversation_anchor_model(script, character_a=characters[0], character_b=characters[1])
    phrases = _validate_plan(plan, model)
    timings = {name: discover_character_timing(audio_folder, str(row["sound_file"])) for name, row in mapping.items()}
    wavs = {name: _wav_duration(audio_folder, str(row["sound_file"])) for name,row in mapping.items()}
    shared_duration = min(wavs[name][1] for name in characters)
    duration_warning = (
        f"Runtime WAV durations differ ({characters[0]}={wavs[characters[0]][1]:.3f}s, {characters[1]}={wavs[characters[1]][1]:.3f}s); "
        f"using the shortest shared duration {shared_duration:.3f}s."
        if abs(wavs[characters[0]][1] - wavs[characters[1]][1]) > 0.02 else ""
    )
    cursors = {name: 0 for name in characters}; anchor_times: dict[str, dict[str, Any]] = {}
    for turn in model.turns:
        actor = next(name for name in characters if speaker_key(name) == speaker_key(turn.speaker))
        timing = timings[actor]
        for anchor in turn.anchors:
            index = cursors[actor]
            if index >= len(timing.words): raise ValueError(f"{actor} {turn.turn_id}: missing timing word for {anchor.text!r} in {timing.path}")
            word = timing.words[index]; actual = str(word.get("norm") or word.get("word") or "")
            if normalize_word(anchor.text) != normalize_word(actual): raise ValueError(f"{actor} {turn.turn_id}: expected word {anchor.text!r}, timing word {actual!r}, source {timing.path}")
            anchor_times[anchor.anchor_id] = {"speaker": turn.speaker, "text": anchor.text, "start": float(word["start"]), "end": float(word["end"]), "timing_source": str(timing.path)}
            cursors[actor] += 1
    for actor, timing in timings.items():
        if cursors[actor] != len(timing.words):
            remaining=timing.words[cursors[actor]:]
            raise ValueError(f"{actor}: {len(remaining)} unexpected remaining timing word(s) in {timing.path}: {remaining[:3]}")
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    timing_path = out / "conversation_anchor_timing.json"; timing_path.write_text(json.dumps(anchor_times, indent=2)+"\n", encoding="utf-8")
    artifacts: dict[str, Any] = {"conversation_anchor_timing": str(timing_path), "characters": {}}
    canonical_timeline = build_canonical_phrase_timeline(phrases, model, anchor_times)
    phrase_timing_path = out / "conversation_phrase_timing.json"
    phrase_timing_path.write_text(json.dumps({"phrases": [{key: value for key, value in item.items() if key != "phrase"} for item in canonical_timeline]}, indent=2) + "\n", encoding="utf-8")
    artifacts["conversation_phrase_timing"] = str(phrase_timing_path)
    for actor in characters:
        events=[]
        for timing in canonical_timeline:
            phrase = timing["phrase"]
            for channel, value in phrase["states"][actor].items():
                if value not in (None, "NONE"):
                    events.append({"phrase_id": phrase["phrase_id"], "source_proposal_id": phrase.get("source_proposal_id"), "speaker": phrase.get("speaker"), "actor": actor, "intent": phrase.get("intent"), "channel": channel, "value": value, "source_char_span": phrase.get("span"), "resolved_time": {"start": timing["canonical_start"], "end": timing["canonical_end"]}, "reason": ((phrase.get("rationale") or {}).get(actor, {}) or {}).get(channel)})
        token = re.sub(r"[^A-Za-z0-9_.-]+", "_", actor).strip("_") or "character"
        path=out/"characters"/token/"semantic_events_resolved.json"; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps({"events":events},indent=2)+"\n",encoding="utf-8")
        source = Path(mapping[actor].get("transcript_path") or (Path(audio_folder) / f"{mapping[actor]['sound_file']}.txt"))
        if not source.is_file(): raise FileNotFoundError(f"{actor}: JALI source transcript not found: {source}")
        annotated, diagnostic = build_dual_speaker_jali_annotation(source.read_text(encoding="utf-8"), phrases, alias=actor, script_name=str(mapping[actor]["script_name"]), mask_only=True)
        target = out / "characters" / token / "jali_speaker_annotated.txt"; target.write_text(annotated, encoding="utf-8")
        diagnostic.update({"sound_file": mapping[actor]["sound_file"], "source_transcript_path": str(source), "annotated_transcript_path": str(target)})
        diagnostic_path = target.with_name("jali_speaker_annotation.json"); diagnostic_path.write_text(json.dumps(diagnostic, indent=2)+"\n", encoding="utf-8")
        artifacts["characters"][actor] = {"semantic_events": str(path), "jali_speaker_annotated": str(target), "jali_speaker_annotation": str(diagnostic_path)}
    manifest={"schema_version":"dual_animation_manifest_v1","characters":characters,"performance_plan_source":str(performance_plan_path),"full_script_source":str(script_source or "<provided script text>"),"fps":float(fps),"character_runtime_mapping":mapping,"wav_durations":{name:{"path":str(wavs[name][0]),"seconds":wavs[name][1]} for name in characters},"shared_duration_seconds":shared_duration,"artifacts":artifacts,"warnings":[duration_warning] if duration_warning else []}
    path=out/"dual_animation_manifest.json"; path.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8"); manifest["manifest_path"]=str(path); return manifest


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--performance-plan",type=Path,required=True); p.add_argument("--script-file",type=Path,required=True); p.add_argument("--audio-folder",type=Path,required=True); p.add_argument("--fps",type=float,required=True); p.add_argument("--runtime-mapping",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); a=p.parse_args(); print(json.dumps(compile_dual_performance_plan(performance_plan_path=a.performance_plan,script=a.script_file.read_text(encoding="utf-8"),script_source=a.script_file,audio_folder=a.audio_folder,fps=a.fps,runtime_mapping=json.loads(a.runtime_mapping.read_text(encoding="utf-8")),output_dir=a.output_dir),indent=2))
if __name__ == "__main__": main()
