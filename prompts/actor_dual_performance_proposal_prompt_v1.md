# Shared Dual-Character Mask Performance Proposal

Characters: {{character_a}}, {{character_b}}
Context: {{context}}

[IMMUTABLE SCRIPT]
{{immutable_script}}

[ANCHORED SCRIPT]
{{anchored_script}}

[SEMANTIC VOCABULARY]
{{semantic_reference}}

{{identity_contract}}

Return exactly `[ANALYZE]`, `[PERFORMANCE]`, and `[REASONS]`, in that order. Use the supplied names exactly; never swap or infer identity from dialogue order, personality, speaker order, examples, or narrative role. Do not output `Nothing`; use `NONE` for inactive channels.

Affect intensity is a JALI Mask percentage. Prefer native JALI 2025 presets: Trace=5, Slight=10, Wooden=20, Stoic=40, Restrained=60, Measured=80, Expressive=100, Forceful=120, Theatrical=140, Excessive=160, Extreme=180, Ludicrous=200. Output numeric percentage only, e.g. `{{character_a}}.affect: Nervous-60`.

`affect` must use only `[SEMANTIC VOCABULARY]` Mask states. Character gaze uses names, e.g. `GAZE-{{character_b}}` and `AVERT-{{character_b}}`; objects and directions remain semantic targets.

```text
[ANALYZE]
{{character_a}} becomes increasingly curious about {{character_b}}.

[PERFORMANCE]
S01
start: w0001
intent: GROWING_CURIOSITY
{{character_a}}.affect: Watchful-80
{{character_a}}.gaze: GAZE-{{character_b}}
{{character_a}}.head: MEDIUM
{{character_a}}.lid: -1
{{character_a}}.blink: NONE
{{character_a}}.blink_suppression: NONE
{{character_b}}.affect: Thinking-60
{{character_b}}.gaze: GAZE-{{character_a}}
{{character_b}}.head: LOW
{{character_b}}.lid: NONE
{{character_b}}.blink: NONE
{{character_b}}.blink_suppression: NONE

[REASONS]
S01
intent: Curiosity becomes a shared conversational beat.
{{character_a}}.affect: attentive contained observation.
{{character_a}}.gaze: studies {{character_b}} directly.
```
