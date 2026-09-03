# System Architecture

## Overview

JALITEST creates editable semantic performance plans for conversational character animation. It separates model-authored semantic choices from deterministic anchor construction, validation, timing resolution, and Maya application.

## Inputs

The author provides Dialogue and optional Acting Direction. Character identity and rig mapping are scene-side configuration, not model-inferred semantics.

## Deterministic Anchor Scaffold

The backend constructs immutable dialogue anchors (`w0001`, `w0002`, and so on) from the supplied dialogue. The anchors define the only locations at which semantic beat changes may occur.

## Semantic Beat Generation

One LLM call receives the anchored dialogue, optional Acting Direction, semantic vocabulary, and identity contract. It returns an initial state plus Semantic Beats; it does not author timestamps, frames, scene nodes, or Maya controls.

## Semantic Beat IR

The strict parser and validator turn model output into Semantic Beat IR. Validation enforces character identity, known anchors, closed executable vocabularies, and the distinction between persistent state and transient eye actions.

## Deterministic Performance Plan Compilation

The deterministic semantic compiler converts the IR into one canonical dual Performance Plan. It normalizes no-op persistent changes while preserving the model-authored semantic decisions and diagnostics.

## Editable Authoring Representation

The Maya UI projects one plan into Character A and Character B Semantic Performance Tags. Animators may make local edits, producing an Edited Performance Plan. Acting Interpretation by Phrase is visible natural-language metadata, not a semantic tag or model chain-of-thought.

## Timing Compilation

The deterministic compilation stage combines the Edited Performance Plan with Speech Timing Alignment. Timing Resolution and Execution Artifact Compilation produce Resolved Semantic Events, a Persistent State Timeline, a JALI-Tagged Transcript, and an Animation Manifest.

## Maya/JALI Application

Maya-side application preserves Native JALI Speech Animation and layers deterministic Semantic Affect, Gaze, Head, and Blink overlays. The final output is Maya Animation.

## Scene Mapping and Look-at Calibration

MAYA SCENE SETUP is a side input to Maya-side application. Character/Rig Mapping and Look-at Calibration turn semantic targets into concrete scene targets. They are not part of the LLM prompt or canonical semantic Performance Plan.

## Runtime vs Semantic Data Boundary

Semantic plans contain dialogue-anchored, editable performance intent. Runtime artifacts contain timing, scene mappings, generated transcripts, manifests, and Maya execution details. Generated runtime data is not source material and should be regenerated rather than checked in.
