# JALITEST LLM Actor-Style Annotator Patch

Unzip this package into the root of `SiaDu/JALITEST`.

It adds the LLM-side prompt building layer without changing the existing compiler/parser/exporter behavior.

## Added files

```text
src/expregaze_jali/actor_context_builder.py
src/expregaze_jali/actor_prompt_builder.py
src/expregaze_jali/build_actor_prompt.py
prompts/actor_performance_annotation_prompt_v2.md
scripts/build_actor_prompt.sh
tests/test_actor_prompt_builder.py
```

## What it does

The new prompt builder creates a compact LLM input from:

1. `data/processed/candidate_sequences/Jali_proto_candidate_sequences.jsonl`
2. a small local window from `data/processed/full_context/tt0032138__full_context.csv`
3. the exact transcript from the candidate's `subtitle_text`
4. a capability profile: `mvp` or `full_actor`
5. raw snippets of `configs/base.yaml` and `configs/jali_emotion_options.yaml`

It does **not** call an LLM API yet. It only writes prompt/context files.

## Usage

Full actor annotation prompt:

```bash
bash scripts/build_actor_prompt.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --profile full_actor
```

Executable MVP prompt, only `g/m/h` tags:

```bash
bash scripts/build_actor_prompt.sh \
  --sequence-id Jali_proto_candidate_001_ProfessorCrystal \
  --profile mvp
```

Outputs:

```text
data/processed/gaze_script/llm_process/{sequence_id}__actor_prompt.txt
data/processed/gaze_script/llm_process/{sequence_id}__context_pack.json
```

## Why this handles full_context safely

It does not send the whole `tt0032138__full_context.csv` to the model.

It uses:

```text
candidate row
+ story_description as a short story card
+ shot window around start_shot_idx/end_shot_idx
```

This gives the annotator enough story/acting context without exploding the token budget.
