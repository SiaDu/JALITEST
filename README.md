# JALITEST

JALITEST is a small ExpreGaze + JALI prototype for generating script-aware performance annotations and compiling them into JALI / Maya-ready outputs.

The current input is **processed MovieNet / ExpreGaze data**, not raw MovieNet. The main processed inputs are candidate sequence files, processed full-context CSV files, JALI/TextGrid word timing files, and optional manually edited exact transcripts.

## Core idea

```text
LLM = actor / performance annotator
code = compiler / resolver
JALI = lipsync, speech/facial baseline, TextGrid alignment
Maya adapter = executor for gaze / actor overlay events
```

The LLM does **not** output JSON, seconds, frames, or Maya controls. It outputs a readable actor-style annotation:

```text
[ANALYZE]
...

[ANNOTATION]
<g1=GAZE-LISTENER><m1=Friendly-70>That's right. Here.

[REASONS]
g1: Listener gaze establishes social control.
m1: Friendly mask keeps Dorothy compliant.
```

The code then compiles this into:

```text
data/processed/gaze_script/{clip}__annotated_for_jali.txt
data/processed/gaze_script/{clip}__gaze_events_resolved.json
data/processed/gaze_script/{clip}__actor_overlay_events.json
```

## Directory layout

```text
data/processed/candidate_sequences/
  Jali_proto_candidate_sequences.jsonl

  Processed MovieNet / ExpreGaze candidate clips. Each record contains a selected
  clip, shot metadata, subtitle text, aligned script dialogue, speakers, and local
  context fields.

data/processed/full_context/
  tt0032138__full_context.csv

  Processed MovieNet / ExpreGaze full-context table. The pipeline uses only a
  local shot window and a short story card, not the whole file.

data/processed/textgrid/
  {clip}__words.jsonl

  Word timing extracted from JALI / TextGrid. This is required for compiling
  readable annotation spans into resolved time ranges.

data/processed/gaze_script/llm_process/
  {clip}__context_pack.json
  {clip}__actor_prompt.txt
  {clip}__performance_annotation.txt
  {clip}__llm_response_meta.json
  {clip}__debug_full_annotation.txt

  LLM inputs, raw LLM output, metadata, and debug files. These are intermediate
  process files and are not directly applied in Maya.

data/processed/gaze_script/
  {clip}__annotated_for_jali.txt
  {clip}__gaze_events_resolved.json
  {clip}__actor_overlay_events.json

  Final executable outputs for JALI / Maya.
```

`data/processed/gaze_script/prompt/` is no longer used.

## Script overview

The project uses numbered scripts. There is intentionally no full-pipeline shell script yet, so each step can be checked before the next one runs.

| Step | Script | Calls LLM? | Main purpose |
|---:|---|---:|---|
| 00 | `scripts/00_parse_textgrid.sh` | No | Parse TextGrid into word timing JSONL. |
| 01 | `scripts/01_build_actor_prompt.sh` | No | Build `context_pack.json` and `actor_prompt.txt`. |
| 02 | `scripts/02_run_actor_llm.sh` | Yes, once | Call OpenAI and write `performance_annotation.txt`. |
| 03 | `scripts/03_compile_actor_annotation.sh` | No | Compile LLM annotation into JALI / gaze / overlay outputs. |
| 04 | `scripts/04_validate_actor_outputs.sh` | No | Validate sections, timing, targets, and output files. |


## Step 00: parse TextGrid

```bash
bash scripts/00_parse_textgrid.sh configs/path_local.yaml
```

This wraps:

```bash
python -m expregaze.data.textgrid_parser --paths-config configs/path_local.yaml
```

Expected output:

```text
data/processed/textgrid/{clip}__words.jsonl
```

This step does not call the LLM.

## Step 01: build actor prompt

```bash
bash scripts/01_build_actor_prompt.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --profile full_actor \
  --overwrite
```

Outputs:

```text
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__context_pack.json
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__actor_prompt.txt
```

This step does not call the LLM.

After this step, inspect the exact transcript before paying for an LLM call:

```bash
code data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__context_pack.json
code data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__actor_prompt.txt
```

The LLM tags only `exact_transcript`. The context pack also includes `subtitle_text` and `aligned_script_dialogue` for reference, but `exact_transcript` is the editable source of truth for annotation.

Optional manually edited transcript:

```bash
bash scripts/01_build_actor_prompt.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --profile full_actor \
  --exact-transcript-file data/input/transcripts/Jali_proto_candidate_001_ProfessorCrystal.txt \
  --overwrite
```

Common parameters:

```text
--sequence-id             Candidate sequence id.
--profile                 mvp or full_actor.
--candidate-jsonl          Processed candidate sequence JSONL.
--full-context-csv         Processed full-context CSV.
--full-context-window      Number of surrounding shots to include.
--no-full-context          Use only the candidate record.
--exact-transcript-file    Optional manually edited exact transcript.
--output-dir               Defaults to data/processed/gaze_script/llm_process.
--overwrite                Allow replacing existing files.
```

## Step 02: run actor LLM

```bash
bash scripts/02_run_actor_llm.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite
```

Outputs:

```text
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__performance_annotation.txt
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__llm_response_meta.json
```

This step calls the LLM exactly once.

`OPENAI_API_KEY` must be available in the WSL shell environment. The variable name is read from `configs/base.yaml`:

```yaml
llm:
  api_key_env: OPENAI_API_KEY
```

Useful `base.yaml` settings:

```yaml
llm:
  provider: openai
  model: gpt-5-mini
  max_output_tokens: 8000
  reasoning_effort: low
  api_key_env: OPENAI_API_KEY
```

If the LLM response is incomplete or missing `[ANALYZE]`, `[ANNOTATION]`, or `[REASONS]`, this step fails and does not write a partial annotation.

## Step 03: compile actor annotation

```bash
bash scripts/03_compile_actor_annotation.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite
```

Inputs:

```text
data/processed/gaze_script/llm_process/{clip}__performance_annotation.txt
data/processed/gaze_script/llm_process/{clip}__context_pack.json
data/processed/textgrid/{clip}__words.jsonl
```

Outputs:

```text
data/processed/gaze_script/{clip}__annotated_for_jali.txt
data/processed/gaze_script/{clip}__gaze_events_resolved.json
data/processed/gaze_script/{clip}__actor_overlay_events.json
data/processed/gaze_script/llm_process/{clip}__debug_full_annotation.txt
```

This step does not call the LLM.

Common parameters:

```text
--sequence-id / --clip-name   Clip id.
--annotation-path             Optional manual annotation path.
--words-jsonl                 Optional word timing JSONL path.
--context-pack                Optional context pack path for target resolution.
--output-dir                  Defaults to data/processed/gaze_script.
--llm-process-dir             Defaults to data/processed/gaze_script/llm_process.
--overwrite                   Allow replacing existing files.
```

## Step 04: validate outputs

```bash
bash scripts/04_validate_actor_outputs.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal
```

Checks:

```text
- required annotation sections exist
- LLM status is completed
- closing tags were stripped by parser if present
- TextGrid alignment warnings
- gaze events have resolved_time
- LISTENER resolves to a concrete character when possible
- generic targets such as OBJECT are flagged for later resolution
- actor overlay output exists
```

Use strict mode to exit non-zero on warnings:

```bash
bash scripts/04_validate_actor_outputs.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --strict
```

## MVP vs full_actor

`mvp` enables only currently stable tags:

```text
gaze:  <g##=MODE-TARGET>
mask:  <m##=MaskName-Strength>
heart: <h##=HeartName-Strength>
```

Use MVP for a conservative JALI + gaze baseline.

`full_actor` additionally enables:

```text
lid_state:          <l##=VALUE>
performative_blink: <pb##=MODE-SUBTYPE>
blink_suppression:  <bs##=SUPPRESS/ALLOW>
```

Use full_actor for the main actor-style research direction. The extra events are exported to:

```text
data/processed/gaze_script/{clip}__actor_overlay_events.json
```

They need a later Maya overlay adapter to become visible animation.

## Target resolution

The prompt encourages concrete gaze targets, but does not hard-restrict the LLM to a code-generated whitelist.

Examples:

```text
<g2=GLANCE-CRYSTAL>     preferred when the prop is clear
<g3=GAZE-LISTENER>      allowed; exporter may resolve LISTENER to DOROTHY
<g4=GLANCE-OBJECT>      allowed when uncertain; marked as needing resolution
```

`gaze_events_resolved.json` includes extra target fields:

```json
{
  "target": "LISTENER",
  "target_role": "LISTENER",
  "target_label": "DOROTHY",
  "target_needs_resolution": false,
  "target_resolution_source": "target_context.role_map"
}
```

If a target cannot be resolved:

```json
{
  "target": "OBJECT",
  "target_role": "OBJECT",
  "target_label": null,
  "target_needs_resolution": true,
  "target_resolution_source": "generic_object_target"
}
```

This lets Maya apply code decide whether to map, skip, or ask for a manual locator mapping.

## Closing tags

Readable actor annotation is intended to use opening state-change tags. If the LLM accidentally writes closing tags such as `</m1>`, the parser strips them from the clean transcript and records them in diagnostics. This prevents closing tags from polluting TextGrid word alignment.

## Quick command sequence

```bash
# 00. Parse TextGrid into words JSONL.
bash scripts/00_parse_textgrid.sh configs/path_local.yaml

# 01. Build context/prompt, then inspect exact_transcript.
bash scripts/01_build_actor_prompt.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --profile full_actor \
  --overwrite

# 02. Run LLM once.
bash scripts/02_run_actor_llm.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite

# 03. Compile outputs.
bash scripts/03_compile_actor_annotation.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite

# 04. Validate outputs.
bash scripts/04_validate_actor_outputs.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal
```
