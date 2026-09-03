# JALITEST

Context-aware, editable semantic performance authoring for conversational character animation in JALI/Maya.

## Overview

```text
Dialogue + Acting Direction
        ↓
Semantic Beat Generation
        ↓
Deterministic Performance Plan Compilation
        ↓
Editable Semantic Performance Tags
        ↓
JALI / Maya Animation
```

JALITEST makes one LLM call to propose semantic performance decisions. Deterministic code builds dialogue anchors, validates the Semantic Beat IR, compiles a canonical Performance Plan, and produces timing and Maya execution artifacts. Animators can inspect and locally edit semantic tags before animation is generated.

## Design Principles

- One LLM call proposes semantic performance decisions.
- Deterministic code owns transcript anchors and executable compilation.
- Animators inspect and locally edit semantic decisions.
- Acting Interpretation is visible metadata, not executable state.
- JALI provides the native speech-animation base.
- Semantic affect, gaze, head, and blink overlays are compiled deterministically.

## Requirements

- Python 3.12 for the backend (`pyproject.toml` specifies the supported range).
- Autodesk Maya 2025 with its bundled Python 3.11 and PySide6 for the Maya UI.
- An installed compatible JALI Maya runtime and character rigs.
- An OpenAI API key configured as `OPENAI_API_KEY` (or the environment variable selected in `configs/llm.yaml`).

JALI and Maya are external dependencies; this repository does not distribute either application/runtime.

## Installation

Create the backend environment with your preferred Python 3.12 environment manager, then install the project dependencies. Set `OPENAI_API_KEY` in the environment or in a local `.env` file. If Maya cannot infer the repository location, set `JALITEST_REPO_ROOT` to the checkout directory.

## Running the Maya UI

In Maya 2025, run `tools/maya/run_performance_plan_ui.py`. It loads the participant-facing authoring UI and launches backend generation in the configured Python environment.

## Authoring Workflow

1. Enter Dialogue and optional Acting Direction.
2. Set Character Mapping and audio inputs.
3. Generate Performance Plan.
4. Inspect or edit Semantic Performance Tags.
5. Optionally inspect Acting Interpretation by Phrase.
6. Complete animation setup and generate animation.

Dual-character authoring uses one canonical dual Performance Plan. The UI projects actor-specific tracks for Character A and Character B; it does not create two independent plans.

## Semantic Performance Representation

The plan records an initial performance state and dialogue-anchored changes. Executable semantic channels include visible affect, persistent gaze, transient glances, head direction, and blink actions. Acting Interpretation by Phrase is natural-language context for the animator and is not executable state.

## System Architecture

See [docs/architecture.md](docs/architecture.md).

## Repository Structure

- `src/expregaze_jali/` — generation, validation, semantic compilation, and timing compilation.
- `tools/maya/` — Maya UI, scene setup, and animation application.
- `prompts/` — active single and dual semantic-performance prompts.
- `configs/` — LLM, semantic vocabulary, and Maya configuration.
- `resources/jali/` — checked-in JALI runtime exports required for listener-mask realization.
- `tests/` — automated tests and stable fixtures.

## User Study Modes

The Maya authoring session supports `NORMAL`, `EDITABLE_PLAN`, and `DIRECT_GENERATION` study modes. These modes control the study experience; they do not change the semantic architecture.

## Testing

```text
pytest -q
```

## External Dependencies

JALI, Maya, and the model API are external dependencies. JALI-derived runtime export files are retained only because current listener-mask realization loads them. Their redistribution/licensing status is not established by this repository.

## Citation

CHI citation placeholder — to be added when publication details are available.
