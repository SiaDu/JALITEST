# JaliTest LLM Runner Patch

This patch adds the missing API caller for the actor-style performance annotator.
The previous patch only built the prompt/context pack. This patch actually calls
OpenAI, saves the annotation, and can optionally compile it into the existing
JALI/gaze outputs.

## Added files

```text
src/expregaze_jali/run_actor_annotator.py
src/expregaze_jali/actor_overlay_event_exporter.py
scripts/run_actor_annotator.sh
tests/test_run_actor_annotator.py
```

## Expected config

It reads `configs/base.yaml`:

```yaml
llm:
  provider: openai
  model: gpt-5-mini
  temperature: 0.2
  max_output_tokens: 3000
  api_key_env: OPENAI_API_KEY
```

The actual API key must be in the WSL environment, not in YAML.

## Dry run

Build prompt/context only, no API call:

```bash
bash scripts/run_actor_annotator.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --profile full_actor \
  --dry-run \
  --overwrite
```

## Run LLM only

```bash
bash scripts/run_actor_annotator.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --profile full_actor \
  --overwrite
```

Outputs:

```text
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__actor_prompt.txt
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__context_pack.json
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__performance_annotation.txt
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__llm_response_meta.json
```

## Run LLM and compile outputs

```bash
bash scripts/run_actor_annotator.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --profile full_actor \
  --compile \
  --overwrite
```

Additional outputs:

```text
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__annotated_for_jali.txt
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__gaze_events_resolved.json
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__actor_overlay_events.json
data/processed/gaze_script/llm_process/Jali_proto_candidate_001_ProfessorCrystal__debug_full_annotation.txt
```

`actor_overlay_events.json` preserves the full-actor tags that current JALI/gaze
exporters do not execute directly yet: `lid_state`, `performative_blink`, and
`blink_suppression`.

## MVP mode

Use this if you want the output to contain only tags that the current JALI/gaze
pipeline already executes:

```bash
bash scripts/run_actor_annotator.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --profile mvp \
  --compile \
  --overwrite
```
