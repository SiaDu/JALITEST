# Actor-Style Performance Annotator Method Notes

This document summarizes the current JALITEST method. It is a method reference, not the command-line user manual.

## Current input scope

The current system starts from **processed MovieNet / ExpreGaze data**, not raw MovieNet.

Main processed inputs:

```text
data/processed/candidate_sequences/Jali_proto_candidate_sequences.jsonl
data/processed/full_context/tt0032138__full_context.csv
data/processed/textgrid/{clip}__words.jsonl
```

The full-context file is not sent to the LLM in full. The code extracts only a local shot window and a short story card to keep the prompt compact.

## System roles

```text
JALI
  - generates lipsync / speech / facial baseline
  - generates TextGrid timing
  - accepts mask / heart transcript tags

LLM actor annotator
  - reads compact context + exact transcript
  - analyzes the performance beat like an actor/director
  - inserts sparse readable tags into the transcript

Compiler
  - parses readable annotation
  - resolves spans against TextGrid words
  - exports JALI text, gaze events, and actor overlay events

Maya adapter
  - later consumes gaze_events_resolved.json and actor_overlay_events.json
  - maps semantic targets to rig controls / locators
```

## Why the LLM does not output JSON

The LLM is used where it is strongest: interpreting social intent, performance beats, and actor choices. It should not be responsible for exact seconds, JSON schema correctness, or Maya control names.

Instead, the LLM outputs readable state-change annotation:

```text
[ANNOTATION]
<g1=GAZE-LISTENER><m1=Friendly-70>That's right.
<g2=GLANCE-CRYSTAL>This is the same genuine, magic, authentic crystal...
```

The compiler handles the structured representation.

## exact_transcript

`exact_transcript` is the text the LLM is allowed to tag. It should match the audio/TextGrid timing source as closely as possible. It can be manually edited before running the LLM.

`subtitle_text` and `aligned_script_dialogue` are preserved in the context pack as reference information, but the annotation should be inserted only into `exact_transcript`.

## MVP vs full_actor

MVP enables:

```text
gaze
mask
heart
```

It is the conservative executable baseline.

full_actor enables:

```text
gaze
mask
heart
lid_state
performative_blink
blink_suppression
```

It is the main research direction, but lid/blink/suppression outputs still need a Maya overlay adapter.

## Target resolution

The code-generated `scene_targets` list is a hint, not a hard vocabulary. The LLM may infer targets that the keyword extractor missed.

The exporter adds target resolution fields to each gaze event:

```json
{
  "target": "LISTENER",
  "target_role": "LISTENER",
  "target_label": "DOROTHY",
  "target_needs_resolution": false
}
```

Generic targets remain allowed:

```json
{
  "target": "OBJECT",
  "target_role": "OBJECT",
  "target_label": null,
  "target_needs_resolution": true
}
```

This keeps the LLM flexible while still telling the Maya stage which targets are unresolved.

## Closing tags

The intended readable annotation grammar is state-change opening tags only. However, LLMs sometimes output closing tags. The parser strips closing tags from the clean transcript and records them in diagnostics, so they do not contaminate TextGrid alignment.

## Output semantics

```text
annotated_for_jali.txt
  Only JALI-compatible mask / heart tags. This can go to JALI transcript workflows.

gaze_events_resolved.json
  Timed semantic gaze events for Maya gaze adapter.

actor_overlay_events.json
  Timed eyelid / performative blink / blink suppression events for later Maya overlay work.

debug_full_annotation.txt
  Parser/resolver summary, clean transcript, and original annotation for debugging.
```
