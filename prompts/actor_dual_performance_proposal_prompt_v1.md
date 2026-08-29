# Sparse Dual-Character Performance Proposal v2

Characters: {{character_a}}, {{character_b}}
Context: {{context}}

[IMMUTABLE SCRIPT]
{{immutable_script}}

[ANCHORED DIALOGUE WITH BACKEND SPEAKER METADATA]
{{anchored_script}}

[SEMANTIC VOCABULARY]
{{semantic_reference}}

{{identity_contract}}

Reason internally about dialogue, Acting Direction, social interaction, motivation, and scene-grounded gaze opportunities; do not output that analysis. Return exactly `[GAZE_TARGETS]`, `[INITIAL]`, then `[CHANGES]`. `[GAZE_TARGETS]` contains at most five uppercase physical scene target identifiers (or `NONE`), never characters, directions, or `GAZE-*` tags. Both actors enter the scene already performing: `[INITIAL]` must contain one block for each actor with a visible `affect`, a persistent `GAZE-*`, and a meaningful `reason`. Initial affect may not be `MASK-NONE`; use a real visible Mask such as `Neutral-60` when appropriate. Initial state is not a word-anchored change or a SPEAK/LISTEN reaction. Initial gaze may not be GLANCE or GAZE-NONE; head is optional and blink is not allowed.

For each actor independently, analyze both speaking behavior and listening behavior. Listeners may react during another actor's utterance: anchor a meaningful listener change to the earliest semantically sufficient heard cue word. Do not automatically wait for sentence completion, dialogue-turn completion, or the listener's next spoken line. Phrase and clause comprehension matter more than punctuation. Do not over-segment; create a listener event only when heard information meaningfully changes the acting state.

Choose later sparse change points by acting meaning: threat-relevant keyword, realization, suspicion increase, hesitation, disclosure, accusation, direct question, eye-contact decision, affect transition, deliberate head pose, or meaningful performative blink. Do not create an event merely because punctuation occurred, a dialogue turn began, or N words elapsed.

Each event has a unique E-number, one actor, an existing word anchor, at least one changed semantic channel, and one concise event-level reason. For one actor, never emit more than one event at the same anchor: combine simultaneous channel changes into one event with one reason. A listener reason should identify the meaningful heard stimulus rather than restating the visible action. Do not create per-channel boilerplate reasons. Emit only changed channels. There is no fixed event count, no event is required at each turn, and one actor changing never implies an event for the other actor.

Allowed channels are affect, gaze, head, and blink. Never output Heart, Lid, blink_suppression, timing, seconds, frames, latency, duration, SPEAK/LISTEN mode, or speaker. The backend derives timing from actor and anchor speaker.

Affect is a valid Mask state plus any positive integer percentage, or MASK-NONE. Neutral is not NONE. Executable gaze is only GAZE-target or GLANCE-target. Targets may be characters, objects, or RIGHT/LEFT/DOWN/DOWN_LEFT/DOWN_RIGHT/UP/UP_LEFT/UP_RIGHT. GAZE-NONE, GLANCE-NONE, and AVERT are never executable authored gaze modes.

Concepts such as avoiding eye contact, averting one's eyes, thinking, recalling, searching for words, guilt, discomfort, hesitation, or suspicion are acting motivations. They should influence the contextual choice of GAZE/GLANCE and target, but are never executable gaze modes. Do not map an emotion or motivation to a fixed direction; choose the target from this scene's acting context. For example, `gaze: GAZE-DOWN` may be motivated by avoiding eye contact, while `gaze: GLANCE-UP_LEFT` may be motivated by briefly trying to recall a detail.

Head is HEAD-UP/DOWN/TILT_LEFT/TILT_RIGHT with SUBTLE/MEDIUM/STRONG, or HEAD-NONE. Authored blink options are only SLOW_BLINK, DOUBLE_BLINK, EYE_CLOSE_HOLD, or EYE_OPEN; never author plain BLINK. SLOW_BLINK and DOUBLE_BLINK execute finite presets. EYE_CLOSE_HOLD remains active until an explicit EYE_OPEN. Semantic anchors never contain timing, duration, or frames.

```text
[GAZE_TARGETS]
NONE
{{character_a}} becomes increasingly curious about {{character_b}}.

[INITIAL]
{{character_a}}
affect: Watchful-80
gaze: GAZE-{{character_b}}
reason: Enters ready to assess the encounter.

{{character_b}}
affect: Nervous-60
gaze: GAZE-{{character_a}}
reason: Enters guarded while concealing relevant knowledge.

[CHANGES]
E001
actor: {{character_b}}
anchor: w0002
gaze: GAZE-DOWN
reason: The heard cue increases discomfort and breaks eye contact.
```
