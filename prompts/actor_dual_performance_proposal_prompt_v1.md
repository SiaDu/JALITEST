# Sparse Dual-Character Performance Proposal v2

Characters: {{character_a}}, {{character_b}}
Acting Direction: {{context}}

[IMMUTABLE SCRIPT]
{{immutable_script}}

[ANCHORED DIALOGUE WITH BACKEND SPEAKER METADATA]
{{anchored_script}}

[SEMANTIC VOCABULARY]
{{semantic_reference}}

{{identity_contract}}

Reason internally about dialogue, Acting Direction, social interaction, motivation, and scene-grounded gaze opportunities; do not output that analysis. Return exactly `[GAZE_TARGETS]`, `[INITIAL]`, then `[CHANGES]`.

[GAZE_TARGETS] is calibration metadata only. It lists at most five uppercase physical non-character scene objects or locations that may need an optional Maya look-at capture, such as `WINDOW`, `FIREPLACE`, or `NEW_HOUSE`. It does NOT list every gaze used in the Performance Plan.

Never place character names, built-in directions (`UP`, `DOWN`, `LEFT`, `RIGHT`, `UP_LEFT`, `UP_RIGHT`, `DOWN_LEFT`, `DOWN_RIGHT`), or executable `GAZE-*` / `GLANCE-*` values under `[GAZE_TARGETS]`. Character gaze and built-in directional gaze require no scene-target calibration and therefore never belong in this section.

If the plan uses only character gaze and/or built-in directional gaze and has no physical non-character scene target requiring calibration, output exactly:

`[GAZE_TARGETS]`
`NONE`

Both actors enter the scene already performing: `[INITIAL]` must contain one block for each actor with a visible `affect`, a persistent `GAZE-*`, and a meaningful `reason`. Initial affect may not be `MASK-NONE`; use a real visible Mask such as `Neutral-60` when appropriate. Initial state is not a word-anchored change or a SPEAK/LISTEN reaction. Initial gaze may not be GLANCE or GAZE-NONE; head is optional and blink is not allowed.

For each actor independently, analyze both speaking behavior and listening behavior. Listeners may react during another actor's utterance: anchor a meaningful listener change to the earliest semantically sufficient heard cue word. Do not automatically wait for sentence completion, dialogue-turn completion, or the listener's next spoken line. Phrase and clause comprehension matter more than punctuation. Do not over-segment; create a listener event only when heard information meaningfully changes the acting state.

Choose later sparse change points by acting meaning: threat-relevant keyword,
realization, suspicion increase, hesitation, disclosure, accusation,
direct question, direct address, attentional shift, object reveal or handoff,
social re-engagement after object inspection, eye-contact decision,
affect transition, deliberate head pose, or meaningful performative blink.
A change point may contain only gaze when gaze is the only semantic channel
that changes. Do not create an event merely because punctuation occurred, a
dialogue turn began, or N words elapsed.

Each event has a unique E-number, one actor, an existing word anchor, at least one changed semantic channel, and one concise event-level reason. For one actor, never emit more than one event at the same anchor: combine simultaneous channel changes into one event with one reason. A listener reason should identify the meaningful heard stimulus rather than restating the visible action. Do not create per-channel boilerplate reasons. Emit only changed channels. There is no fixed event count, no event is required at each turn, and one actor changing never implies an event for the other actor.

Allowed channels are affect, gaze, head, and blink. Never output Heart, Lid, blink_suppression, timing, seconds, frames, latency, duration, SPEAK/LISTEN mode, or speaker. The backend derives timing from actor and anchor speaker.

Affect is a valid Mask state plus any positive integer percentage, or MASK-NONE. Neutral is not NONE. Executable gaze is only GAZE-target or GLANCE-target. Targets may be characters, objects, or RIGHT/LEFT/DOWN/DOWN_LEFT/DOWN_RIGHT/UP/UP_LEFT/UP_RIGHT. GAZE-NONE, GLANCE-NONE, and AVERT are never executable authored gaze modes.

GAZE and GLANCE have different temporal semantics. `GAZE-target` establishes a new persistent fixation and remains the actor's active gaze until a later persistent `GAZE-*` change. Use GAZE only when the actor should keep attending to that target. `GLANCE-target` is a brief temporary shift to a target and automatically returns to the actor's currently active persistent gaze; GLANCE does not replace that persistent gaze. Use GLANCE for a quick check, an involuntary look, a brief reaction, or a momentary break of eye contact when attention should return afterward.

ATTENTION TRACKING AND GAZE COVERAGE

Treat gaze as a continuously tracked attention state, not as an occasional accessory to affect. For each actor, silently track the current persistent `GAZE-*` throughout the scene and reconsider it whenever the actor's attention meaningfully shifts.

At each meaningful beat, ask: what is this actor attending to now — the interlocutor, a physical object or location, another available scene target, or an internal thought? If the answer differs from the actor's current persistent gaze, emit a gaze change even when affect, head, and blink do not change. A gaze-only event is valid and desirable when attention changes.

Strong gaze-change opportunities include:
- a direct question or direct address that re-engages the interlocutor;
- an object being shown, handed over, inspected, or explicitly referenced;
- returning from object inspection to the interlocutor;
- praise, accusation, persuasion, confession, challenge, or another socially important statement where eye contact becomes meaningful;
- checking another person's reaction;
- shifting between multiple scene objects or people;
- briefly disengaging from social attention for thought, memory, hesitation, guilt, or self-consciousness.

Persistent object gaze must not accidentally continue through later social interaction. If an actor is currently in `GAZE-OBJECT` and later directly questions, praises, confronts, appeals to, or meaningfully responds to another person, reconsider the gaze. Usually use `GAZE-PERSON` when social attention should remain there, or `GLANCE-PERSON` when the actor only checks that person briefly before returning to the object.

Likewise, when an actor looks at a person and a concrete object becomes the new focus of inspection, demonstration, or discussion, consider whether the gaze should move to that object.

Use `GLANCE-*` for temporary checks that return automatically to the current persistent gaze. Use `GAZE-*` when the new attentional target should remain active.

Sparsity means avoiding redundant or unmotivated gaze changes; it does not mean minimizing the number of gaze decisions. Do not omit a meaningful gaze-only event merely because affect remains unchanged.

Before producing the final answer, perform a silent gaze-coverage pass for each actor:
1. track the persistent gaze from `[INITIAL]` through every `GAZE-*` event;
2. inspect direct questions, direct addresses, object reveals or handoffs, social re-engagement, listener reactions, and internal-attention beats;
3. add a gaze event where the attentional target genuinely changes;
4. remove redundant gaze events where the target has not changed.

There is still no fixed gaze-event count. Do not create gaze changes merely because a dialogue turn starts or because time has passed.

INTERNAL ATTENTION AND DIRECTIONAL GAZE

Built-in directional gaze values may express internal attention when the actor is not attending to a physical scene target. These built-in directions are executable gaze choices, not [GAZE_TARGETS] calibration candidates. Use them only inside an event gaze field, for example `gaze: GLANCE-UP_RIGHT` or `gaze: GLANCE-DOWN`.

Consider a brief directional GLANCE when the dialogue implies active remembering, mental search, visual imagery, working something out, hesitation, self-conscious withdrawal, or emotionally loaded recollection.

For internal memory search or imagery, an upward or upward-diagonal aversion (`GLANCE-UP`, `GLANCE-UP_LEFT`, or `GLANCE-UP_RIGHT`) is often readable as thinking or remembering. For guilt, shame, embarrassment, discomfort, or avoidance of eye contact, a brief downward aversion (`GLANCE-DOWN` or a downward diagonal) may be appropriate when supported by the social context.

These are expressive acting priors, not fixed psychological codes. Never assign a deterministic meaning such as remembered=LEFT or imagined=RIGHT. Choose among plausible directions based on the scene, character, previous gaze motion, and visual variety, and avoid repeatedly using the same direction.

Do not omit gaze merely because there is no physical gaze target. If a meaningful beat involves a clear shift from external social attention to internal attention, consider whether a directional GLANCE makes that shift visible.

Example:

`gaze: GLANCE-UP_RIGHT`
is a valid executable event gaze.

But UP_RIGHT must NOT appear as a bare line under [GAZE_TARGETS].

Likewise, `gaze: GAZE-{{character_b}}` is valid executable character gaze, but `{{character_b}}`
must NOT appear under `[GAZE_TARGETS]`.

Before emitting a gaze channel, track the actor's current persistent gaze from `[INITIAL]` and prior `GAZE-*` events. Never repeat the same active `GAZE-*` value. If the persistent gaze is unchanged and only another channel changes, omit `gaze` from that event. A prior `GLANCE-*` does not change the persistent gaze.

Concepts such as avoiding eye contact, averting one's eyes, thinking, recalling, searching for words, guilt, discomfort, hesitation, or suspicion are acting motivations. They should influence the contextual choice of GAZE/GLANCE and target, but are never executable gaze modes. Do not map an emotion or motivation to a fixed direction; choose the target from this scene's acting context. For example, `gaze: GLANCE-DOWN` may be motivated by briefly breaking eye contact before returning to the prior fixation, while `gaze: GLANCE-UP_LEFT` may be motivated by briefly trying to recall a detail. Use a persistent `GAZE-*` only when the new target should remain the actor's focus.

Head is HEAD-UP/DOWN/TILT_LEFT/TILT_RIGHT with SUBTLE/MEDIUM/STRONG, or HEAD-NONE. Authored blink options are only SLOW_BLINK, DOUBLE_BLINK, EYE_CLOSE_HOLD, or EYE_OPEN; never author plain BLINK. SLOW_BLINK and DOUBLE_BLINK execute finite presets. EYE_CLOSE_HOLD remains active until an explicit EYE_OPEN. Semantic anchors never contain timing, duration, or frames.

```text
[GAZE_TARGETS]
NONE

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
gaze: GLANCE-DOWN
reason: The heard cue briefly breaks eye contact before attention returns to the prior fixation.
```
