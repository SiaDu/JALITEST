# Shared Dual-Character Actor Performance Proposal v1

Interpret both characters together against one immutable conversation scaffold.

Character A: {{character_a}}
Character B: {{character_b}}
Aliases: {{alias_map}}
Optional context:
{{context}}

[IMMUTABLE SCRIPT]
{{immutable_script}}

[ANCHORED SCRIPT]
{{anchored_script}}

[SEMANTIC VOCABULARY]
{{semantic_reference}}

Return exactly `[ANALYZE]`, `[PERFORMANCE]`, and `[REASONS]`, in that order.
Make one LLM proposal for the whole conversation. Never copy dialogue, provide end anchors, generate
XML, JSON, source IDs, character offsets, Maya controls, timing, frames, or seconds.

Every dialogue turn needs a phrase at its first anchor. Add a shared phrase start when the performance
meaningfully changes for A, B, or both. Code derives each end from the next start or turn end.
Every phrase has one shared conversational beat intent and a complete resolved state for both A and B.
Do not omit fields, use inheritance, or output `Nothing`; use `NONE` for inactive channels.
Use digit integers only for affect and heart intensity: `Smug-30`, not `Smug-thirty`.

Actor-level interpretation is open vocabulary. In `[ANALYZE]`, intent, and `[REASONS]`, freely describe
acting with concepts such as curious, warm, suspicious, hesitant, interested, affectionate, or guarded.
However, `affect` and `heart` are executable JALI channels with closed vocabularies. For `affect`, use
only a visible affect listed in `[SEMANTIC VOCABULARY]`; for `heart`, use only a listed heart state.
Do not put descriptive concepts such as Curious, Warm, Interested, Concerned, or Suspicious directly in
`affect` unless they explicitly appear in the supplied visible-affect list. Any other value is invalid in
these executable fields. If an acting concept has no direct JALI Mask label, preserve it in intent/reasoning
and realize it with an appropriate supported combination of visible affect, gaze, head, lid, blink, and heart.

Example: Agnes may become increasingly curious about Will in `[ANALYZE]`, have the shared intent
`GROWING_CURIOSITY_ABOUT_WILL`, and use `A.affect: Watchful-35`, `A.gaze: GAZE-B`, and `A.head: MEDIUM`.
This is an acting choice for the scene, not a fixed Curious-to-Watchful mapping.

```text
[ANALYZE]
Agnes becomes increasingly curious about Will, but keeps the curiosity socially contained.

[PERFORMANCE]
S09
start: w0042
intent: GROWING_CURIOSITY_ABOUT_WILL
A.affect: Watchful-35
A.heart: NONE
A.gaze: GAZE-B
A.head: MEDIUM
A.lid: -1
A.blink: NONE
A.blink_suppression: NONE
B.affect: Thinking-30
B.heart: NONE
B.gaze: GAZE-A
B.head: LOW
B.lid: NONE
B.blink: NONE
B.blink_suppression: NONE

[REASONS]
S09
intent: Curiosity becomes a shared conversational beat.
A.affect: Her curiosity appears outwardly as attentive, contained observation.
A.gaze: She studies Will more directly as her interest grows.
```

Exact grammar:

```text
[ANALYZE]
free-text interpretation of the interaction and both characters

[PERFORMANCE]
S01
start: w0001
intent: SHARED_CONVERSATIONAL_BEAT
A.affect: STATE-INTEGER_0_TO_100 | NONE
A.heart: STATE-INTEGER_0_TO_100 | NONE
A.gaze: MODE-TARGET | NONE
A.head: NONE | LOW | MEDIUM | HIGH | FULL
A.lid: SUPPORTED_INTEGER | NONE
A.blink: NONE | SLOW_BLINK | EYE_CLOSE_HOLD | DOUBLE_BLINK | BLINK_CLUSTER
A.blink_suppression: NONE | SUPPRESS
B.affect: STATE-INTEGER_0_TO_100 | NONE
B.heart: STATE-INTEGER_0_TO_100 | NONE
B.gaze: MODE-TARGET | NONE
B.head: NONE | LOW | MEDIUM | HIGH | FULL
B.lid: SUPPORTED_INTEGER | NONE
B.blink: NONE | SLOW_BLINK | EYE_CLOSE_HOLD | DOUBLE_BLINK | BLINK_CLUSTER
B.blink_suppression: NONE | SUPPRESS

[REASONS]
S01
intent: natural-language explanation, not another intent label
A.affect: reason for A's active affect
A.gaze: reason for A's active gaze
A.head: reason for A's meaningful head choice
B.affect: reason for B's active affect
B.gaze: reason for B's active gaze
B.head: reason for B's meaningful head choice
```

Repeat the S block for every shared phrase. In `[REASONS]`, each `S##` appears once as a rationale
block header; do not repeat `S##.` before every field. Under `intent:`, write a natural-language
explanation, not another intent label. Reasons explain the proposal and never redefine `[PERFORMANCE]`.
Explain intent, active affect/heart/gaze, meaningful head choices, and active blink behavior; lid rationale
is optional unless it represents an important acting choice. Gaze values normally use `MODE-TARGET`.
For social eye-contact avoidance, prefer `AVERT-B` when A avoids B and `AVERT-A` when B avoids A.
For spatially meaningful avoidance, use an explicit direction such as `AVERT-DOWN`, `AVERT-DOWN_LEFT`,
or `AVERT-UP_RIGHT`. Do not output bare `GAZE` or `GLANCE`. Bare `AVERT` is tolerated only as shorthand
for the unambiguous social counterpart; prefer the explicit A/B form. Allowed gaze modes are `GAZE`,
`GLANCE`, and `AVERT`. Use A/B for character targets; known character
names and `CHARACTER_NAME` are accepted. Objects use `OBJECT_NAME`; directions use supplied values.
