# Maya Performance Authoring UI Specification

## Status and Scope

This document is the source of truth for the Phase 1 Maya Performance Plan authoring UI. The target runtime is Autodesk Maya 2025 with Python 3.11.4, PySide6, and shiboken6. Maya-side code must not import the Python 3.12 backend package or require extra Maya-side packages.

The primary user is an animator. The primary interface is a readable, editable Semantic Performance Score; the existing event/table inspector remains available only as an Advanced / Debug view.

## Product Workflow

1. Enter or load the input script, audio folder, character mappings, and optional semantic look-at target mappings.
2. Select **Generate Performance Plan**.
3. Review and optionally edit the Acting Interpretation.
4. Select **Regenerate Plan** when the interpretation changes.
5. Review and optionally edit the Semantic Performance Score.
6. Inspect all original AI reasons associated with any numbered phrase.
7. Select **Generate Animation** only after the score is valid.

In Phase 1, the three named actions are visible but backend generation and animation execution remain explicit placeholders. No LLM call is invented inside Maya.

## Data Architecture

The canonical representation remains the structured Performance Plan JSON:

```text
Performance Plan JSON
        <->
Semantic Score formatter / parser
        <->
editable Maya text representation
```

The Semantic Performance Score is an authoring projection, never a replacement persistence format. Loading creates a model over a deep copy of the complete plan. Applying a valid score updates only represented semantic values in that copy. It preserves source tags, character spans, rationale, evidence, diagnostics, locks, and unknown JSON fields. Saving always writes canonical Performance Plan JSON.

Each formatted phrase records the canonical spans that contributed to it. A human edit updates the corresponding existing semantic span when that category is represented. Phase 1 does not synthesize timing or animation data. A phrase whose authored tags differ from its original proposal is recorded as manually edited in canonical plan metadata; original rationale remains unchanged.

## Phase 1.1 Extension

Phase 1.1 completes the semantic authoring and session-persistence boundary without adding backend execution.

The three data layers are deliberately separate:

```text
Performance Plan  = semantic performance decisions and Acting Interpretation
Authoring Session = Maya nodes, script-character mappings, audio folder, and UI mode
Timing Layer      = future TextGrid/time/frame resolution
```

### Acting Interpretation Persistence

The canonical Performance Plan has an optional top-level `acting_interpretation` string. The normalizer copies the actor annotation parser's existing `[ANALYZE]` section verbatim into this field; it does not summarize or regenerate it. Maya loads this value into the editable Acting Interpretation field and commits the edited text before saving. Older plans without the field load with an empty editor.

### Complete Authorable Semantic State

Every authorable canonical decision is visible in the normal Semantic Performance Score: phrase/event intent, lid state, visible affect, hidden affect (heart), gaze, head involvement, performative blink, and blink suppression.

Single-character form:

```text
1. {WITHHOLD_THE_INSULT}
   <l-3><Polite-50><HEART-Angry-70><GAZE-GULCH><HEAD-LOW>
   And now -- well, being a Christian woman, I can't say it!
```

Intent is a phrase heading in braces, not an angle-bracket tag. Hidden affect uses `<HEART-State-Strength>` and remains distinct from visible affect. Head involvement uses only `<HEAD-NONE>`, `<HEAD-LOW>`, `<HEAD-MEDIUM>`, `<HEAD-HIGH>`, or `<HEAD-FULL>`; numeric involvement is never exposed in the normal authoring view.

The canonical display order is intent heading, lid, visible affect, hidden affect, gaze, head, then blink behaviors.

Dual-character form uses one shared Phase 1.1 intent heading followed by complete A/B states:

```text
1. {REASSURE_AND_INVITE}
   A:<l-1><Friendly-66><HEART-Hopeful-20><GAZE-B><HEAD-MEDIUM> |
   B:<l1><Curious-20><HEART-Cautious-15><GAZE-A><HEAD-LOW>
   A: That's right.
```

Separate A/B intents and production dual-character generation remain deferred.

### All-Channel Phrase Boundaries

Phrase boundaries are the sorted union of event/intent boundaries and the starts and ends of visible affect, hidden affect, gaze, head involvement, lid, performative blink, and blink-suppression spans. A change only to hidden affect or head therefore creates a readable phrase boundary. Every resulting phrase repeats its complete resolved semantic state; no category relies on hidden inheritance.

### Authoring-Session Sidecar

Maya execution/session mappings are stored outside the Performance Plan at:

```text
data/processed/performance_plan/{sequence_id}__authoring_session.json
```

The sidecar schema is `authoring_session_v0` and contains `sequence_id`, single/dual `mode`, `audio_folder`, character alias/script-name/Maya-node mappings, and semantic-target/Maya-node mappings. Unknown sidecar fields are preserved when practical. Loading a plan restores a matching sidecar when present; saving an edited plan also saves the sidecar. Missing scene nodes remain visible as text and may produce a warning, but are never silently deleted. Session mappings must never be inserted into the semantic Performance Plan.

## Main UI

The dialog is a vertically scrollable authoring surface with these sections.

### Setup

- **Input Script**: editable multiline text.
- **Input Audio Folder**: path field and **Select Folder** button.
- **Mode**: Single Character or Dual Character, with a maximum of two authored characters.
- **Character Mapping**: explicit script character name mapped to a Maya rig/node. Scene selection can populate the rig field.
- **Potential Look-at Target Mapping**: semantic target name mapped to Maya geometry or locator, with **+ Add Look-at Target** and scene-selection support.
- **Generate Performance Plan**: visible Phase 1 placeholder, separate from data loading.
- Existing Performance Plan JSON loading remains available.

Character names must match the names used by the script/context. Look-at mappings provide deterministic future resolution for tags such as `<GAZE-CRYSTAL>` but do not apply animation in Phase 1.

### Acting Interpretation

An editable multiline field holds scene, affective-state, and narrative-intent interpretation. **Regenerate Plan** is a visible Phase 1 placeholder. Editing this field does not trigger an automatic backend or LLM call.

### Semantic Performance Score

One editable plain-text component supports both modes. Internal IDs, closing tags, JSON, `source_tag`, `char_start`, and `char_end` are absent from this normal authoring view.

Single-character form:

```text
1. {REASSURE_AND_INVITE}
   <l-1><Friendly-66><HEART-Hopeful-20><GAZE-LISTENER><HEAD-MEDIUM>
   That's right.

2. {DIRECT_ATTENTION}
   <l-1><Friendly-66><HEART-Hopeful-20><GLANCE-DOWN><HEAD-LOW>
   Here.
```

Dual-character form:

```text
1. {REASSURE_AND_INVITE}
   A:<l-1><Friendly-66><HEART-Hopeful-20><GAZE-B><HEAD-MEDIUM> |
   B:<l1><Curious-10><HEART-Cautious-15><GAZE-A><HEAD-LOW>
   A: That's right.

2. {DIRECT_ATTENTION}
   A:<l-1><Friendly-66><HEART-Hopeful-20><GLANCE-DOWN><HEAD-LOW> |
   B:<l1><Curious-10><HEART-Cautious-15><GAZE-A><HEAD-LOW>
   A: Here.
```

Dual mode changes formatting and parsing, not the UI component. `A` and `B` map to the two explicit script characters. The dialogue line identifies the speaker; both characters' resolved states are shown simultaneously.

### Reason by Phrase

The animator enters or selects a phrase number. The view shows the phrase text and every original AI rationale associated with the canonical spans contributing to that phrase, grouped by behavior. It does not require selecting individual behaviors.

If the phrase was manually edited, the view states: “Phrase manually edited. AI rationale corresponds to the original proposal.” It then continues to display the preserved original rationale.

### Advanced / Debug

The existing event list, semantic tables, intent/locks editor, raw span fields, rationale, and diagnostics are retained in a collapsible Advanced / Debug area. This view can expose implementation details, but it is not the primary authoring interface.

## Phrase Construction and Resolved State

A phrase is a deterministic readable local performance unit. In Phase 1.1 boundaries are the sorted union of:

- event span starts and ends; and
- starts and ends of visible affect, hidden affect, gaze, head involvement, lid, and blink spans within events.

Empty intervals are discarded. Phrase text is sliced from canonical event text using canonical character offsets; event text itself is used as a safe fallback when offsets are incomplete or inconsistent. Adjacent intervals with identical resolved state may remain separate at meaningful event boundaries.

At each phrase interval, every active category is resolved and printed. A resolved state remains active only while its canonical semantic span covers the phrase. If a span explicitly ends, the state is absent until another span begins. Inherited state is repeated in the human-facing score only when the canonical span actually continues across that phrase. This applies to visible affect, hidden affect, gaze, head involvement, and lid state. Intent comes from the containing canonical event. Performative blink and blink suppression remain interval-based and are printed on every phrase interval they overlap. Thus every phrase is self-contained without extending state beyond its canonical span.

## Semantic Score Grammar

Whitespace and blank lines are flexible, but phrase numbers must be positive, unique, contiguous, and ordered from 1.

```text
single-phrase := NUMBER "." intent NEWLINE tag+ NEWLINE dialogue
dual-phrase   := NUMBER "." intent NEWLINE "A:" tag+ "|" NEWLINE? "B:" tag+ NEWLINE speaker ":" dialogue
intent        := "{" INTENT_NAME "}"
speaker       := "A" | "B"
tag           := "<" affect | hidden-affect | gaze | head | lid | blink ">"
affect        := NAME "-" INTEGER_0_TO_100
hidden-affect := "HEART-" NAME "-" INTEGER_0_TO_100
gaze          := ("GAZE" | "GLANCE" | "AVERT") "-" TARGET
head          := "HEAD-" ("NONE" | "LOW" | "MEDIUM" | "HIGH" | "FULL")
lid           := "l" SIGNED_NUMBER
blink         := "SLOW_BLINK" | "EYE_CLOSE_HOLD" | "SUPPRESS"
```

`NAME` is a known affect state learned from the loaded plan and/or supplied by the model's allowed vocabulary. `TARGET` is a known semantic target learned from the loaded plan, character aliases (`A`, `B`, `SPEAKER`, `LISTENER`), standard directional targets, or an explicitly configured look-at mapping. Tags use canonical uppercase behavior tokens for gaze/blink, display lids as `<l-3>` or `<l0>`, and display affect intensity as integer percent (for example `<Friendly-66>`).

No implementation IDs or closing tags are emitted. Within a character state, the canonical order is lid, visible affect, hidden affect, gaze, head, then blink behavior. A category may occur at most once per character per phrase, except that distinct blink behaviors may coexist when canonically present.

## Validation

Parsing is deterministic and never guesses an unknown behavior. Validation occurs before applying or saving score edits and before Generate Animation can become available. Errors identify the phrase whenever possible, for example:

```text
Phrase 6: Unknown behavior <GASE-LISTENER>
```

Validation covers malformed phrase headers, missing dialogue, numbering errors, malformed/unclosed tags, unknown categories or values, out-of-range affect intensity, duplicate exclusive categories, invalid dual A/B columns, and invalid speaker labels. Invalid text leaves the canonical plan unchanged and blocks save/animation acceptance.

Known affect states and semantic gaze targets are derived from the loaded plan plus explicit stable defaults. UI look-at mappings extend the allowed target set. Unknown typed names are not silently corrected.

## Applying Human Edits and Provenance

Applying a valid score compares each parsed phrase with the original formatted proposal. Changed represented categories are written through to the canonical structured spans associated with the phrase. Existing normalized fields (`state`, `intensity`, `mode`, `target`, `lid_state`, `value`) remain coherent.

The model adds or updates a top-level `authoring` object containing Semantic Score metadata and a list of manually edited phrase records. Each record identifies the stable phrase number/event association and the categories changed. This metadata is additive and does not delete unknown authoring metadata.

Original rationale is never rewritten or presented as newly generated rationale. No LLM is called after score editing. Reason lookup warns that a manual edit has overridden the proposal while preserving and displaying all original reasons.

## Save Contract

**Save Edited Plan** and **Save Edited Plan As...** write canonical JSON using the existing edited-path and JSON helpers. Saving first validates and applies the current score. The source file is not overwritten by the default edited path. All unknown fields, source tags, character offsets, original rationale, diagnostics, and unrepresented canonical behavior remain preserved.

## Deferred / Not in Phase 1

- Generate Performance Plan backend invocation
- Regenerate Plan backend invocation
- Generate Animation execution
- JALI integration
- gaze/head/blink application
- TextGrid/time/frame conversion
- animation preview
- user-study logging

Also deferred are selective LLM regeneration, multi-agent behavior, a timeline UI, and production dual-character plan generation. Phase 1 supplies deterministic dual-character formatting/parsing architecture and fixtures only.

## Phase 1 Acceptance Criteria

- The Maya UI opens under Maya 2025 / PySide6 and safely replaces an older window.
- Setup, character mapping, look-at mapping, Acting Interpretation, and exact named action buttons are present.
- Existing plan JSON loads and renders as a numbered simplified score.
- Every phrase prints its complete resolved state.
- The score is editable and phrase-specific validation blocks invalid application/save.
- Phrase reason lookup returns all original associated rationales.
- Manual edits are tracked while original rationale and canonical provenance remain intact.
- Save emits canonical structured JSON through the existing save/path utilities.
- Advanced / Debug retains the useful existing inspector.
- Single- and dual-character formatter/parser behavior is testable without Maya, PySide6, or the backend package.
- The normal pytest suite passes without Maya.
