# JALITEST

JALITEST is a small ExpreGaze + JALI prototype for generating script-aware performance annotations and compiling them into JALI / Maya-ready outputs.

The current input is **processed MovieNet / ExpreGaze data**, not raw MovieNet. The main processed inputs are candidate sequence files, processed full-context CSV files, JALI/TextGrid word timing files, and optional manually edited exact transcripts from the local JALI sound folder.

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
<g1=GAZE-LISTENER><m1=Friendly-70>That's right. Here.</m1></g1>

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

  Word timing extracted from JALI / TextGrid. This is required only when compiling
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
| 00 | `scripts/00_build_actor_prompt.sh` | No | Build `context_pack.json` and `actor_prompt.txt`; inspect/edit exact transcript before calling the LLM. |
| 01 | `scripts/01_run_actor_llm.sh` | Yes, once | Call OpenAI and write `performance_annotation.txt`. |
| 02 | `scripts/02_parse_textgrid.sh` | No | Parse TextGrid into word timing JSONL. Required before Step 03, not before Step 00. |
| 03 | `scripts/03_compile_actor_annotation.sh` | No | Compile LLM annotation into JALI / gaze / overlay outputs. |
| 04 | `scripts/04_validate_actor_outputs.sh` | No | Validate sections, timing, targets, and output files. |

## Step 00: build actor prompt

```bash
bash scripts/00_build_actor_prompt.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite
```

Outputs:

```text
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__context_pack.json
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__actor_prompt.txt
```

This step does not call the LLM and does not require `data/processed/textgrid/{clip}__words.jsonl`.

After this step, inspect the exact transcript before paying for an LLM call:

```bash
code data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__context_pack.json
code data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__actor_prompt.txt
```

The LLM tags only `exact_transcript`. The context pack also includes `subtitle_text` and `aligned_script_dialogue` for reference, but `exact_transcript` is the editable source of truth for annotation.

### Exact transcript source

By default, Step 00 tries to load a manually edited exact transcript from `configs/path_local.yaml`:

```yaml
jali:
  project_root: /mnt/e/maya_project/JALI_test
  input_dir: scenes/sounds_proto1
```

For sequence id `Jali_proto_candidate_001_ProfessorCrystal`, this resolves to:

```text
/mnt/e/maya_project/JALI_test/scenes/sounds_proto1/Jali_proto_candidate_001_ProfessorCrystal.txt
```

This is the WSL path for:

```text
E:\maya_project\JALI_test\scenes\sounds_proto1\Jali_proto_candidate_001_ProfessorCrystal.txt
```

If the file exists, its content becomes `context_pack.exact_transcript`. If it is missing, Step 00 prints a warning and falls back to the candidate subtitle text.

You can override the transcript path manually:

```bash
bash scripts/00_build_actor_prompt.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --exact-transcript-file data/input/transcripts/Jali_proto_candidate_001_ProfessorCrystal.txt \
  --overwrite
```

You can disable the auto transcript lookup and force candidate subtitle text:

```bash
bash scripts/00_build_actor_prompt.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --no-auto-exact-transcript-file \
  --overwrite
```

Common parameters:

```text
--sequence-id                    Candidate sequence id.
--candidate-jsonl                 Processed candidate sequence JSONL.
--full-context-csv                Processed full-context CSV.
--full-context-window             Number of surrounding shots to include.
--no-full-context                 Use only the candidate record.
--paths-config                    Defaults to configs/path_local.yaml.
--exact-transcript-file           Optional manually edited exact transcript.
--no-auto-exact-transcript-file   Do not auto-read JALI transcript from paths_config.
--output-dir                      Defaults to data/processed/gaze_script/llm_process.
--overwrite                       Allow replacing existing files.
```

## Step 01: run actor LLM

```bash
bash scripts/01_run_actor_llm.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite
```

Input:

```text
data/processed/gaze_script/llm_process/{clip}__actor_prompt.txt
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

Common parameters:

```text
--sequence-id          Clip / sequence id.
--base-config          LLM config YAML. Defaults to configs/base.yaml.
--llm-process-dir      Directory containing actor_prompt.txt and LLM process files.
                       Defaults to data/processed/gaze_script/llm_process.
--prompt-path          Optional manual prompt path. If omitted, reads:
                       {llm_process_dir}/{clip}__actor_prompt.txt
--output-annotation    Optional annotation output path. If omitted, writes:
                       {llm_process_dir}/{clip}__performance_annotation.txt
--output-meta          Optional metadata output path. If omitted, writes:
                       {llm_process_dir}/{clip}__llm_response_meta.json
--overwrite            Allow replacing existing annotation/meta files.
```

## Step 02: parse TextGrid

```bash
bash scripts/02_parse_textgrid.sh configs/path_local.yaml
```

This wraps:

```bash
python -m expregaze_jali.textgrid_parser --paths-config configs/path_local.yaml
```

Expected output:

```text
data/processed/textgrid/{clip}__words.jsonl
```

This step does not call the LLM. It is required before Step 03 compile, but Step 00 and Step 01 can run before it.

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
- closing tags were parsed as explicit span ends if present
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

## Annotation tag set

The project uses one actor-style annotation mode.

Allowed tags:

```text
<g##=MODE-TARGET>...</g##>          gaze
<m##=MaskName-Strength>...</m##>    visible facial mask
<h##=HeartName-Strength>...</h##>   hidden heart / inner undercurrent
<l##=VALUE>...</l##>                sustained eyelid state
<pb##=MODE>...</pb##>               performative blink / intentional eye-close beat
<bs##=SUPPRESS/ALLOW>...</bs##>     blink suppression state
```

Lid/blink/blink-suppression tags are allowed but optional. Use them only when they express an intentional acting beat.

## Target resolution

The prompt encourages concrete gaze targets, but does not hard-restrict the LLM to a code-generated whitelist.

Examples:

```text
<g2=GLANCE-CRYSTAL>     preferred when the prop is clear
<g3=GAZE-LISTENER>      allowed; exporter may resolve LISTENER to DOROTHY
<g4=GLANCE-OBJECT>      allowed only when uncertain; marked as needing resolution
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

Closing tags are allowed in readable actor annotation. The parser strips the closing tag text from the clean transcript and uses the closing tag position as an explicit event end, so local beats do not have to continue until the next same-type tag.

## Quick command sequence

```bash
# 00. Build context/prompt, then inspect exact_transcript.
bash scripts/00_build_actor_prompt.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite

# 01. Run LLM once.
bash scripts/01_run_actor_llm.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite

# 02. Parse TextGrid into words JSONL before compiling.
bash scripts/02_parse_textgrid.sh configs/path_local.yaml

# 03. Compile outputs.
bash scripts/03_compile_actor_annotation.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite

# 04. Validate outputs.
bash scripts/04_validate_actor_outputs.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal
```
