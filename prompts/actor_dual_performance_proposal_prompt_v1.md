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

Return exactly `[ANALYZE]` then `[CHANGES]`. Use the supplied names exactly. Choose sparse change points by acting meaning: threat-relevant keyword, realization, suspicion increase, hesitation, disclosure, accusation, direct question, eye-contact decision, affect transition, deliberate head pose, or meaningful performative blink. Do not create an event merely because punctuation occurred, a dialogue turn began, or N words elapsed.

Each event has a unique E-number, one actor, an existing word anchor, at least one changed semantic channel, and one concise event-level reason. Emit only changed channels. There is no fixed event count, no event is required at each turn, and one actor changing never implies an event for the other actor.

Allowed channels are affect, gaze, head, and blink. Never output Heart, Lid, blink_suppression, timing, seconds, frames, latency, duration, SPEAK/LISTEN mode, or speaker. The backend derives timing from actor and anchor speaker.

Affect is a valid Mask state plus any positive integer percentage, or MASK-NONE. Neutral is not NONE. Gaze is GAZE-target, GLANCE-target, AVERT-character/object, directional AVERT-RIGHT/LEFT/DOWN/DOWN_LEFT/DOWN_RIGHT/UP/UP_LEFT/UP_RIGHT, or GAZE-NONE. Never use a bare direction. Head is HEAD-UP/DOWN/TILT_LEFT/TILT_RIGHT with SUBTLE/MEDIUM/STRONG, or HEAD-NONE. Blink is BLINK, SLOW_BLINK, DOUBLE_BLINK, or EYE_CLOSE_HOLD and is instantaneous.

```text
[ANALYZE]
{{character_a}} becomes increasingly curious about {{character_b}}.

[CHANGES]
E001
actor: {{character_a}}
anchor: w0001
affect: Watchful-80
gaze: GAZE-{{character_b}}
reason: Establishes controlled scrutiny.

E002
actor: {{character_b}}
anchor: w0002
blink: SLOW_BLINK
reason: Marks deliberate consideration.
```
