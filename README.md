# JALITEST

JALITEST is a small ExpreGaze + JALI prototype for generating script-aware performance annotations and compiling them into JALI / Maya-ready outputs.

## Current pipeline

```text
full_context + shot_range + exact transcript
  -> actor prompt
  -> LLM actor-style annotation
  -> JALI transcript tags + resolved gaze events + eye/blink overlay events
  -> optional Maya application scripts
```

The LLM does **not** output JSON, seconds, frames, or Maya controls. It outputs a readable actor-style transcript annotation. Code parses that annotation and compiles executable outputs.

## Config layout

The configs are intentionally split by purpose:

```text
configs/
  llm.yaml                                  # Step 01 OpenAI runtime settings only
  project.yaml                              # stable repo paths and prompt extra files
  jali_emotion_options.yaml                 # prompt-only JALI mask / heart reference
  performance_rules.yaml                    # prompt-only acting/tagging rules
  sequences/
    Jali_proto_candidate_001_ProfessorCrystal.yaml
    example.yaml                            # copy this for a new sequence
  maya/
    valleygirl.yaml                         # Maya adapter settings for gaze/eye/JALI injection
```

Removed legacy config files:

```text
configs/base.yaml
configs/path_local.yaml
configs/path_local.example.yaml
configs/tt0032138.yaml
configs/runs/*.yaml
configs/maya/jali_proto_candidate_001_*.yaml
```

For a new sequence, copy `configs/sequences/example.yaml` and edit only that file:

```yaml
sequence:
  sequence_id: YOUR_SEQUENCE_ID
  movie_id: tt0032138
  movie_name: The Wizard of Oz
  local_window: 3
  shot_range:
    start_shot_idx: 38
    end_shot_idx: 38
  fps: 30.0
  clip_end_frame: null

jali:
  project_root: /mnt/e/maya_project/JALI_test
  input_dir: scenes/sounds_proto1
```

Derived paths are automatic:

```text
full_context_file = data/processed/full_context/{movie_id}__full_context.csv
transcript_file   = {jali.project_root}/{jali.input_dir}/{clip_name}.txt
textgrid_file     = {jali.project_root}/{jali.input_dir}/{clip_name}.Textgrid
words_jsonl       = data/processed/textgrid/{clip_name}__words.jsonl
actor_prompt      = data/processed/gaze_script/llm_process/{clip_name}__actor_prompt.txt
```

## Numbered scripts

| Step | Script | Calls LLM? | Purpose |
|---:|---|---:|---|
| 00 | `scripts/00_build_actor_prompt.sh` | No | Build `context_pack.json` and `actor_prompt.txt`. |
| 01 | `scripts/01_run_actor_llm.sh` | Yes | Call OpenAI and write `performance_annotation.txt`. |
| 02 | `scripts/02_parse_textgrid.sh` | No | Parse JALI/Praat TextGrid into word timing JSONL. |
| 03 | `scripts/03_compile_actor_annotation.sh` | No | Compile annotation into JALI / gaze / overlay outputs. |
| 04 | `scripts/04_validate_actor_outputs.sh` | No | Validate sections, timing, targets, and outputs. |

## Step 00: build actor prompt

```bash
bash scripts/00_build_actor_prompt.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite
```

Outputs:

```text
data/processed/gaze_script/llm_process/{clip}__context_pack.json
data/processed/gaze_script/llm_process/{clip}__actor_prompt.txt
```

Step 00 uses:

```text
configs/project.yaml
configs/sequences/<sequence>.yaml
configs/jali_emotion_options.yaml
configs/performance_rules.yaml
```

## Step 01: run actor LLM

```bash
bash scripts/01_run_actor_llm.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite
```

LLM runtime settings live in `configs/llm.yaml`:

```yaml
llm:
  provider: openai
  model: gpt-5-mini
  temperature: 0.2
  max_output_tokens: 8000
  reasoning_effort: low
  api_key_env: OPENAI_API_KEY
  max_retries: 3
  request_sleep_sec: 2
```

You can override it with:

```bash
bash scripts/01_run_actor_llm.sh --llm-config configs/llm.yaml --overwrite
```

`--base-config` is still accepted as a deprecated alias for `--llm-config`.

## Step 02: parse TextGrid

```bash
bash scripts/02_parse_textgrid.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
```

Output paths are derived from `clip_name / sequence_id` automatically.

## Step 03: compile actor annotation

```bash
bash scripts/03_compile_actor_annotation.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --overwrite
```

Outputs:

```text
data/processed/gaze_script/{clip}__annotated_for_jali.txt
data/processed/gaze_script/{clip}__gaze_events_resolved.json
data/processed/gaze_script/{clip}__actor_overlay_events.json
data/processed/gaze_script/llm_process/{clip}__debug_full_annotation.txt
```

## Step 04: validate outputs

```bash
bash scripts/04_validate_actor_outputs.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal
```

Use strict mode to fail on warnings:

```bash
bash scripts/04_validate_actor_outputs.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --strict
```

## Maya helper scripts

```text
exec(open(r"\\wsl.localhost\Ubuntu-24.04\home\sia\JaliTest\tools\maya\run_create_gaze_targets.py", encoding="utf-8").read())

exec(open(r"\\wsl.localhost\Ubuntu-24.04\home\sia\JaliTest\tools\maya\run_apply_gaze_events.py", encoding="utf-8").read())

exec(open(r"\\wsl.localhost\Ubuntu-24.04\home\sia\JaliTest\tools\maya\run_apply_eye_performance_events.py", encoding="utf-8").read())
```

Maya-side runner scripts live in:

```text
tools/maya/
```

They are not Python package source. `src/` is reserved for importable package code under `src/expregaze_jali/`.

Default Maya config:

```text
configs/maya/valleygirl.yaml
```

The three Maya runners all read that single config:

```text
tools/maya/run_apply_jali_annotation.py
tools/maya/run_apply_gaze_events.py
tools/maya/run_apply_eye_performance_events.py
```

## Annotation tag set

```text
<g##=MODE-TARGET>...</g##>          gaze
<m##=MaskName-Strength>...</m##>    visible facial mask
<h##=HeartName-Strength>...</h##>   hidden heart / inner undercurrent
<l##=VALUE>...</l##>                sustained eyelid state
<pb##=MODE>...</pb##>               performative blink / intentional eye-close beat
<bs##=SUPPRESS/ALLOW>...</bs##>     blink suppression state
```

`l/pb/bs` are actor overlay tags, not JALI-native mask/heart tags.
