# Maya Performance Authoring UI Specification

## Status and Scope

This document is the source of truth for the Phase 1 Maya Performance Plan authoring UI. The target runtime is Autodesk Maya 2025 with Python 3.11.4, PySide6, and shiboken6. Maya-side code must not import the Python 3.12 backend package or require extra Maya-side packages.

The primary user is an animator. The primary interface is a readable, editable Semantic Performance Score; the existing event/table inspector remains available only as an Advanced / Debug view.

For the CHI user study, canonical JSON and file-loading details are hidden from the normal participant-facing Authoring tab. Participants are expected to enter setup information and use **Generate Performance Plan**; the resulting Semantic Performance Score is the primary representation they inspect and edit. Manual loading and saving of pre-generated canonical JSON remain developer capabilities in Advanced / Debug.

## Product Workflow

1. Enter the required script, optional free-text context, audio folder, character mappings, and optional semantic look-at target mappings.
2. Select **Generate Performance Plan**.
3. Review and optionally edit the Acting Interpretation.
4. Select **Regenerate Plan** when the interpretation changes.
5. Review and optionally edit the Semantic Performance Score.
6. Inspect all original AI reasons associated with any numbered phrase.
7. Select **Generate Animation** only after the score is valid.

**Generate Performance Plan** invokes the separate Python 3.12 HCI backend asynchronously. **Generate Animation** asynchronously compiles the currently edited canonical plan and then applies the resulting artifacts in Maya. **Regenerate Plan** remains an explicit placeholder. Maya never executes an LLM request in its Python 3.11 process.

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

Each formatted phrase records the canonical spans that contributed to it. A human edit updates the corresponding existing semantic span when that category is represented. Timing and Maya artifacts remain outside the canonical semantic plan and are derived only when Generate Animation runs. A phrase whose authored tags differ from its original proposal is recorded as manually edited in canonical plan metadata; original rationale remains unchanged.

## Phase 1.1 Extension

Phase 1.1 established the semantic authoring and session-persistence boundary. The subsequent HCI generation bridge adds external backend execution without changing that boundary.

The three data layers are deliberately separate:

```text
Performance Plan  = semantic performance decisions and Acting Interpretation
Authoring Session = script/context, Maya nodes, mappings, audio folder, and UI mode
Timing Layer      = derived TextGrid/words/time/frame animation artifacts
```

### Acting Interpretation Persistence

The canonical Performance Plan has an optional top-level `acting_interpretation` string. The legacy normalizer copies the actor annotation parser's `[ANALYZE]` section, while the anchor-grounded HCI builder copies the proposal's `[ANALYZE]` section; neither path summarizes or regenerates it. Maya loads this value into the editable Acting Interpretation field and commits the edited text before saving. Older plans without the field load with an empty editor.

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

### Semantic Vocabulary Layers

The system deliberately separates three semantic layers. **Actor Interpretation, Intent, and Rationale**
use open acting language: an author or model may describe a character as curious, warm, interested,
suspicious, hesitant, guarded, awkward, affectionate, assessing, or defensive. These descriptions do
not imply a one-to-one executable facial label.

**Visible Affect** is a closed JALI Mask vocabulary: `Neutral`, `Polite`, `Friendly`, `Sassy`, `Smug`,
`Cocky`, `Nervous`, `Panicky`, `Thinking`, `Scheming`, `Devious`, `Devilish`, `Provoked`, `Angered`,
`Dislike`, `Disgust`, `Singing_Serene`, `Watchful`, `Intimidating`, `Confused`, `Lost`, or `NONE`.

**Hidden Affect / Heart** is a separate closed JALI Heart vocabulary: `Angry`, `Sad`, `Disgusted`,
`Afraid`, `Contempt`, `Surprised`, `Happy`, or `NONE`. `Nothing` is an internal JALI default and is
never emitted by HCI semantic authoring.

Both executable lists are defined once in `configs/semantic_vocabulary.json` (`semantic_vocabulary_v1`),
which is readable by the backend and Maya without YAML support. Backend tests assert that it stays in
lockstep with the corresponding JALI Mask and first-version Heart lists in `jali_emotion_options.yaml`.

For example, an actor interpretation may say, “Agnes grows curious.” The executable semantic plan can
still be `{GROWING_CURIOSITY_ABOUT_WILL}` with `<Watchful-35><GAZE-B><HEAD-MEDIUM>`. The system does
not map Curious to Watchful automatically; the acting proposal selects an appropriate supported
combination for the specific scene.

Dual-character form uses one shared Phase 1.1 intent heading followed by complete A/B states:

```text
1. {REASSURE_AND_INVITE}
   A:<l-1><Friendly-66><HEART-Happy-20><GAZE-B><HEAD-MEDIUM> |
   B:<l1><Thinking-20><HEART-Happy-15><GAZE-A><HEAD-LOW>
   A: That's right.
```

Separate A/B intents remain deferred. Production dual semantic generation uses one shared phrase-level
conversational/performance beat intent and separate complete A/B states.

### All-Channel Phrase Boundaries

Phrase boundaries are the sorted union of event/intent boundaries and the starts and ends of visible affect, hidden affect, gaze, head involvement, lid, performative blink, and blink-suppression spans. A change only to hidden affect or head therefore creates a readable phrase boundary. Every resulting phrase repeats its complete resolved semantic state; no category relies on hidden inheritance.

### Authoring-Session Sidecar

Maya execution/session mappings are stored outside the Performance Plan at:

```text
data/processed/performance_plan/{sequence_id}__authoring_session.json
```

The sidecar schema is `authoring_session_v0` and contains `sequence_id`, single/dual `mode`, participant-entered `input_script` and `input_context`, `audio_folder`, character alias/script-name/Maya-node mappings, and semantic-target/Maya-node mappings. Unknown sidecar fields are preserved when practical, and older sidecars without script/context remain valid. Loading a plan restores the exact script and context when present. Generating or saving a plan updates its sidecar. Missing scene nodes remain visible as text and may produce a warning, but are never silently deleted. Session fields must never be inserted into the semantic Performance Plan.

## HCI Generation Architecture

The participant-facing semantic inputs are deliberately small: **Script** is required, **Context** is optional free text, and the active script character comes from Character Mapping. Audio, rig mapping, and look-at mapping remain animation/session inputs rather than prompt dataset context.

### Dual Shared Performance Phrase Architecture

Dual semantic authoring is conversation-level and is not derived from two independently generated or
independently segmented single-character plans:

```text
Conversation
    ↓
Shared Anchor Scaffold
    ↓
ONE LLM call
    ↓
Shared phrase boundaries
    ↓
A + B simultaneous semantic states
    ↓
Editable Dual Performance Plan
```

The first Script Character field defines `A`; the second defines `B`. Every labeled script line must
belong to one of those characters. The deterministic scaffold anchors the entire conversation. Each
turn has a phrase at its first word, and the model may add starts inside a turn whenever either
character's performance changes. Code derives phrase ends from the next start in that turn or the turn
end, preserving exact transcript characters and offsets without gaps or overlaps.

One dual proposal block contains a shared intent plus complete A/B state:

```text
S01
start: w0001
intent: FORMAL_GREETING_AND_MUTUAL_ASSESSMENT
A.affect: Polite-42
A.heart: NONE
A.gaze: GAZE-B
A.head: MEDIUM
A.lid: -1
A.blink: NONE
A.blink_suppression: NONE
B.affect: Watchful-20
B.heart: NONE
B.gaze: GAZE-A
B.head: LOW
B.lid: -1
B.blink: NONE
B.blink_suppression: NONE
```

The production schema is `dual_performance_plan_v0`. Its top-level `characters` maps A/B to script
names, and each canonical `phrases` row contains the code-derived speaker, exact span, shared intent,
complete `states.A` and `states.B`, preserved A/B rationale, and locks. It has no timing or Maya-node
fields. Maya rig and semantic target mappings may remain empty while generating, editing, and saving
this plan. Dual Generate Animation remains disconnected.

```text
Participant Inputs
    Script
    Optional Context
    Character / Look-at mappings
          ↓
Immutable Transcript
          ↓
Deterministic Anchor Scaffold
          ↓
LLM chooses Performance Phrase START boundaries
          ↓
Code derives complete contiguous phrase intervals
          ↓
Editable Canonical Performance Plan JSON (internal)
          ↓
Semantic Performance Score
          ↓
Animator Editing
```

### Anchor-Grounded Semantic Proposal

A **Performance Phrase** is a contiguous span of the immutable transcript over which the proposed semantic performance state remains coherent. The LLM proposes only each phrase's start boundary using deterministic word-anchor IDs. Code derives the phrase end from the next start in that turn or its utterance end. A phrase may cover a complete utterance or only part of one, and may begin or end within a sentence. Exact character positions are always resolved by code.

The anchor scaffold parses labeled dialogue into turns and assigns global whitespace-delimited IDs (`w0001`, `w0002`, ...). Speaker labels are metadata and are not semantic anchor text. Every anchor records its turn, speaker, exact source substring, and global `char_start`/`char_end`. If the input has no labels, the complete script is one turn owned by the target character. The current labeled prototype accepts at most two characters. `A` denotes the target and `B` the one other speaker when present.

The HCI Input Script is immutable clean dialogue. Before anchoring or any LLM call, the backend rejects recognized legacy/JALI performance tags instead of stripping them, and requires each labeled dialogue turn to occupy its own physical line.

The proposal has exactly these line-oriented sections:

```text
[ANALYZE]
free-text acting interpretation

[PERFORMANCE]
S01
start: w0008
intent: HESITATE_AND_BUY_TIME
affect: Nervous-55
heart: NONE
gaze: AVERT-DOWN
head: LOW
lid: -1
blink: NONE
blink_suppression: NONE

[REASONS]
S01.intent: Hesitation buys time before answering.
S01.affect: Nervousness makes uncertainty visible.
```

All nine fields are required in every S-block; inactive optional channels use `NONE`, while `head: NONE` is a valid explicit zero-involvement choice. There is no proposal-state inheritance. `Nothing` is an internal JALI backend value and is normalized to inactive `NONE`, never to prior-state inheritance; `Neutral` remains an active visible affect. For example, `heart: Happy-28` followed by `heart: NONE` explicitly ends the Happy state at that boundary. The LLM decides whether performance changes and where each new state starts, but never copies transcript text and never generates phrase ends, source tag IDs, closing tags, character offsets, JSON, timing, frames, seconds, or Maya controls.

For each target-character turn, the first proposed start must be its first anchor; later starts must be strictly ordered and target-owned. The deterministic layer constructs the complete contiguous partition: `char_start` is a phrase start anchor, and `char_end` is the next phrase start in that turn or the utterance end. It therefore decides where the previous phrase ends, exact text, whitespace, punctuation, `char_start`/`char_end`, and canonical source tags. This preserves every target utterance without asking the LLM to calculate end anchors or reproduce transcript text.

Semantic values are normalized only for harmless case and spelling-format variance. Intent becomes uppercase snake case. Affect and heart states must exist in the JALI vocabulary; intensities must be 0–100. Gaze supports `GAZE`, `GLANCE`, and `AVERT` with A/B, known dialogue-character names, `CHARACTER_*`, direction, or concrete `OBJECT_*` targets. Known character names normalize deterministically to A/B before canonical `GAZE-CHARACTER_*` construction. Head, lid, blink, and suppression use their documented finite values. Unknown character targets fail with phrase-specific diagnostics rather than being guessed. The builder generates `i##`, `m##`, `h##`, `g##`, `hd##`, `l##`, `pb##`, and `bs##` IDs deterministically. Phrase-local spans preserve phrase-specific rationale; optional proposal provenance retains the normalized anchor proposal.

The HCI path uses `expregaze_jali.generate_performance_plan`, the v3 proposal prompt, JALI emotion options, and the existing reusable one-call OpenAI request logic. It directly builds the canonical plan and does not call the XML annotation parser or normalizer. The legacy actor-style XML prompt, parser, normalizer, and dataset commands remain separate and supported. The HCI path does not use sequence configuration, `movie_id`, `movie_name`, full-context CSV, shot ranges, local/context windows, or dataset-derived context.

Each run receives an automatically generated internal run ID, retained in the compatible `sequence_id` field for output naming and provenance but never entered by or shown to participants. Runtime artifacts are written beneath `data/processed/hci_runs/<run_id>/` as `actor_prompt.txt`, `anchored_script.txt`, `anchor_map.json`, `performance_proposal.txt`, `llm_response_meta.json`, and `performance_plan.json`. Existing canonical plans and older run folders remain loadable; normal Maya authoring does not expose anchor IDs.

Maya writes long script/context input to UTF-8 runtime files and starts the Python 3.12 backend with `QProcess`, using `JALITEST_BACKEND_PYTHON` or `<repo>/.venv/Scripts/python.exe`. The process is asynchronous. Backend stdout/stderr is retained in Advanced / Debug, failure never loads stale output, and successful generation automatically loads the new canonical plan into Acting Interpretation, Semantic Performance Score, Reason by Phrase, and Advanced / Debug.

## HCI Animation Architecture

Generate Animation uses the current human-edited score, not the original LLM annotation or semantic proposal:

```text
Current edited Semantic Performance Score
          ↓ validate and apply
Runtime canonical Performance Plan
          ↓ expregaze_jali.compile_performance_plan
Deterministic JALI / gaze / eye / head artifacts
          ↓ explicit Maya apply functions and UI mappings
Maya animation
```

Before compilation, Maya validates the editor text and applies it to the in-memory canonical plan. It writes that exact result to `animation/performance_plan_runtime.json`; this runtime plan is the compiler's source of truth. The compiler reads neither `performance_annotation.txt` nor `performance_proposal.txt`, so human edits to affect, hidden affect, gaze, lid, blink, head, or intent are present in its resolved semantic artifacts. Intent and head remain explicit in the resolved/debug artifacts; head application is reported as deferred because no Maya head applier currently exists.

The selected Input Audio Folder supplies alignment without sequence configuration. Discovery deterministically prefers one `*words*.jsonl` file, otherwise one `.TextGrid`/`.textgrid` file with a `words` tier. Missing or ambiguous timing files stop generation with a clear error; timing is never guessed. Maya scene FPS supplies seconds-to-frame conversion.

Compilation runs asynchronously in the Python 3.12 backend and writes beneath the plan's `animation/` directory: `annotated_for_jali.txt`, `gaze_events_resolved.json`, `eye_performance_events.json`, `head_events_resolved.json`, `semantic_events_resolved.json`, `compile_from_plan_debug.txt`, and `animation_manifest.json`. On success, Maya applies JALI affect, gaze, lid, and blink through explicit artifact paths. Directional gaze targets use rig configuration offsets; semantic object/character targets use the participant's look-at mappings. For example, an execution mapping `HAWK → Maya node` satisfies canonical `OBJECT_HAWK`; a missing mapping fails Generate Animation preflight with a clear semantic-target message, never Performance Plan generation.

The HCI animation path does not use sequence config, MovieNet identifiers, shot ranges, full-context CSV, local context windows, or `JALITEST_SEQUENCE_ID`. Single-character animation is supported. Dual-character Generate Animation is explicitly blocked until independent per-character plans and apply routing exist.

## Main UI

The dialog is a vertically scrollable authoring surface with these sections.

### Setup

- **Input Script**: editable multiline text.
- **Context (Optional)**: editable free text for scene, story, character, or performance context; it may be empty.
- **Input Audio Folder**: path field and **Select Folder** button.
- **Mode**: Single Character or Dual Character, with a maximum of two authored characters.
- **Character Mapping**: explicit script character name mapped to a Maya rig/node. Scene selection can populate the rig field.
- **Potential Look-at Target Mapping**: semantic target name mapped to Maya geometry or locator, with **+ Add Look-at Target** and scene-selection support.
- **Generate Performance Plan**: the main participant entry point; it asynchronously invokes the HCI backend and loads the resulting plan.

Character names must match the names used by the script/context. Generate Animation requires the active character's Maya rig/node mapping. Look-at mappings resolve semantic targets such as `<GAZE-CRYSTAL>`; built-in directions use configured offsets without object mappings.

### Acting Interpretation

An editable multiline field holds scene, affective-state, and narrative-intent interpretation. **Regenerate Plan** is a visible Phase 1 placeholder. Editing this field does not trigger an automatic backend or LLM call.

### Semantic Performance Score

One editable plain-text component supports both modes. Internal IDs, closing tags, JSON, `source_tag`, `char_start`, and `char_end` are absent from this normal authoring view.

Single-character form:

```text
1. {REASSURE_AND_INVITE}
   <l-1><Friendly-66><HEART-Happy-20><GAZE-LISTENER><HEAD-MEDIUM>
   That's right.

2. {DIRECT_ATTENTION}
   <l-1><Friendly-66><HEART-Happy-20><GLANCE-DOWN><HEAD-LOW>
   Here.
```

Dual-character form:

```text
1. {REASSURE_AND_INVITE}
   A:<l-1><Friendly-66><HEART-Happy-20><GAZE-B><HEAD-MEDIUM> |
   B:<l1><Thinking-10><HEART-Happy-15><GAZE-A><HEAD-LOW>
   A: That's right.

2. {DIRECT_ATTENTION}
   A:<l-1><Friendly-66><HEART-Happy-20><GLANCE-DOWN><HEAD-LOW> |
   B:<l1><Thinking-10><HEART-Happy-15><GAZE-A><HEAD-LOW>
   A: Here.
```

Dual mode uses the same UI component over one `dual_performance_plan_v0`. `A` and `B` map to the two explicit script characters. The dialogue speaker is derived from the phrase's anchor turn rather than supplied by the LLM; both characters' resolved states are shown simultaneously on one line separated by ` | `. Inactive tags, including `HEAD-NONE`, are omitted from this human-facing projection while remaining explicit in canonical state.

### Reason by Phrase

The animator enters or selects a phrase number. The view shows the phrase text and every original AI rationale associated with the canonical spans contributing to that phrase, grouped by behavior. It does not require selecting individual behaviors.

If the phrase was manually edited, the view states: “Phrase manually edited. AI rationale corresponds to the original proposal.” It then continues to display the preserved original rationale.

### Advanced / Debug

The existing event list, semantic tables, intent/locks editor, raw span fields, rationale, and diagnostics are retained in the Advanced / Debug tab. This view can expose implementation details, but it is not the primary authoring interface. It contains **Load Existing Plan...**, **Save Performance Plan**, and **Save Performance Plan As...** controls for developers to load and persist canonical Performance Plan JSON while testing pre-generated plans. These file controls do not appear in the participant-facing Authoring tab.

## Phrase Construction and Resolved State

A phrase is a deterministic readable local performance unit. In Phase 1.1 boundaries are the sorted union of:

- event span starts and ends; and
- starts and ends of visible affect, hidden affect, gaze, head involvement, lid, and blink spans within events.

Empty intervals are discarded. Phrase text is sliced from canonical event text using canonical character offsets; event text itself is used as a safe fallback when offsets are incomplete or inconsistent. Adjacent intervals with identical resolved state may remain separate at meaningful event boundaries.

At each phrase interval, every active category is resolved and printed. A resolved state remains active only while its canonical semantic span covers the phrase. If a span explicitly ends, the state is absent until another span begins. Inherited state is repeated in the human-facing score only when the canonical span actually continues across that phrase. This applies to visible affect, hidden affect, gaze, head involvement, and lid state. Intent comes from the containing canonical event. Performative blink and blink suppression remain interval-based and are printed on every phrase interval they overlap. Thus every phrase is self-contained without extending state beyond its canonical span.

## Semantic Score Grammar

Whitespace and blank lines are flexible, but phrase numbers must be positive, unique, contiguous, and ordered from 1.

```text
single-phrase := NUMBER "." intent NEWLINE [tag+ NEWLINE] dialogue
dual-phrase   := NUMBER "." intent NEWLINE "A:" tag* "|" NEWLINE? "B:" tag* NEWLINE speaker ":" dialogue
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

Semantic behavior tags are optional. When a single-character phrase has no active behavior channels, the formatter omits the semantic-state line:

```text
1. {HESITATE}
   I don't know.
```

The first body line is parsed as semantic state only when it begins with `<`; otherwise it is dialogue. Dialogue is always required. Dual mode always retains explicit A/B columns so either side can be empty without ambiguity:

```text
1. {LISTEN}
   A: |
   B:<GAZE-A>
   A: Hmm.
```

Visible-affect `NAME` must be in the closed shared JALI Mask vocabulary; hidden-affect `NAME` must be
in the separate closed shared JALI Heart vocabulary. Existing unsupported historical plan values may be
displayed for correction, but do not expand either executable list. `TARGET` is a known semantic target
learned from the loaded plan, character aliases (`A`, `B`, `SPEAKER`, `LISTENER`), standard directional
targets, or an explicitly configured look-at mapping. Tags use canonical uppercase behavior tokens for
gaze/blink, display lids as `<l-3>` or `<l0>`, and display affect intensity as integer percent (for
example `<Friendly-66>`).

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

The Advanced / Debug **Save Performance Plan** and **Save Performance Plan As...** controls write canonical JSON using the existing edited-path and JSON helpers. Saving first validates and applies the current score. The source file is not overwritten by the default edited path. All unknown fields, source tags, character offsets, original rationale, diagnostics, and unrepresented canonical behavior remain preserved. The normal user-study workflow proceeds from **Generate Performance Plan** to Semantic Performance Score editing and **Generate Animation** without exposing persistence format or file controls.

## Deferred / Not in Phase 1

- Regenerate Plan backend invocation
- Maya head-involvement application (head events are compiled and reported)
- production dual-character animation generation/application
- animation preview
- user-study logging

Also deferred are selective LLM regeneration, multi-agent behavior, a timeline UI, and compilation of a
dual authoring plan into per-character execution plans. Production dual semantic authoring already uses
one shared transcript-anchor phrase scaffold; it never independently segments two plans and later
forces their phrase counts to match.

## Phase 1 Acceptance Criteria

- The Maya UI opens under Maya 2025 / PySide6 and safely replaces an older window.
- Setup, character mapping, look-at mapping, Acting Interpretation, and exact named action buttons are present.
- Script-only and script-plus-optional-context generation use the external HCI backend without dataset configuration.
- Generate Performance Plan does not block Maya and automatically loads successful output.
- Dual Generate Performance Plan makes one LLM call over the shared conversation scaffold and loads one `dual_performance_plan_v0` without requiring Maya mappings.
- Generate Animation validates and applies dirty score edits to a runtime canonical plan before compilation.
- Generate Animation compiles without the original annotation/proposal or dataset/sequence configuration and applies JALI, gaze, and eye artifacts in Maya.
- Script and Context persist in the authoring-session sidecar and restore on load.
- Timing discovery supports words JSONL and TextGrid and fails clearly when neither is present.
- Existing plan JSON loads through Advanced / Debug and renders as a numbered simplified score.
- Every phrase prints its complete resolved state.
- The score is editable and phrase-specific validation blocks invalid application/save.
- Phrase reason lookup returns all original associated rationales.
- Manual edits are tracked while original rationale and canonical provenance remain intact.
- Save emits canonical structured JSON through the existing save/path utilities.
- Advanced / Debug retains the useful existing inspector.
- Single- and dual-character formatter/parser behavior and single-character animation command/compilation behavior are testable without Maya or PySide6.
- The normal pytest suite passes without Maya.
