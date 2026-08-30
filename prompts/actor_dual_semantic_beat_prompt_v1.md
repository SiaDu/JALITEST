# Dual Semantic Beat IR v1.1

Characters: {{character_a}}, {{character_b}}
Acting Direction: {{context}}

[IMMUTABLE SCRIPT]
{{immutable_script}}

[ANCHORED DIALOGUE WITH BACKEND SPEAKER METADATA]
{{anchored_script}}

[SEMANTIC VOCABULARY]
{{semantic_reference}}

{{identity_contract}}

Only {{character_a}} and {{character_b}} are performance actors. A third person mentioned in dialogue or Acting Direction may be an attention target but never an INITIAL actor or beat actor.

Return exactly [INITIAL], then [BEATS]. Do not output analysis, [GAZE_TARGETS], gaze, GAZE-*, GLANCE-*, or reason fields.

Use this small grammar:

```text
[INITIAL]
{{character_a}}
affect: MASK-INTENSITY
focus: TARGET
acting: natural-language acting interpretation

{{character_b}}
affect: MASK-INTENSITY
focus: TARGET
acting: natural-language acting interpretation

[BEATS]
E001
actor: {{character_a}}
trigger: w0001
acting: A direct question makes the actor briefly check the other person's reaction.
eye_action: brief_check {{character_b}}

E002
actor: {{character_b}}
trigger: w0002
acting: The heard disclosure raises visible anxiety without changing attention.
affect: Nervous-75

E003
actor: {{character_a}}
trigger: w0003
acting: The threat raises visible anxiety and briefly breaks eye contact.
affect: Nervous-80
eye_action: brief_check DOWN
```

INITIAL requires both actors. Initial focus must be `focus: TARGET`. In [BEATS], affect, focus, eye_action, head, and blink are all optional. A beat may contain one or multiple changed semantic channels. Emit only channels that genuinely change at that beat. If multiple channels change at the same meaningful beat, combine them into the same beat rather than creating separate beats at the same anchor. Do not add affect, focus, eye_action, head, or blink merely to make a beat look complete. `focus` and `eye_action` cannot both appear in the same beat.

CRITICAL GAZE FIELD EXCLUSIVITY: Never output both `focus:` and `eye_action:` in one beat, even when both seem actorly relevant. The final Performance Plan has only one executable gaze channel at an anchor. If persistent focus changes, use `focus: TARGET` and omit eye_action. If persistent focus does not change, use `eye_action: brief_check TARGET` and omit focus. Do not create a second beat at the same anchor to work around this rule. For example, this is INVALID:

```text
focus: NORA
eye_action: brief_check DOWN
```

Choose one field for that beat; use a later distinct meaningful anchor only when a separate action is genuinely supported.

`focus: TARGET` answers what person, object, or location the actor is persistently attending to. It changes the persistent focus. `eye_action: brief_check TARGET` answers whether the actor briefly moves their eyes away from that current focus. It is temporary and automatically returns to the persistent focus. Do not emit a later focus merely to return after an eye_action.

Affect is a strict closed vocabulary. Never invent, paraphrase, or substitute an affect label. If the desired emotion is not present, choose the closest AVAILABLE listed Mask state. Intensities are any positive integer percentage. Initial affect must be visible and not MASK-NONE. In [BEATS], affect may be MASK-NONE when the persistent semantic affect should end. Head and blink use only the listed executable vocabulary; blink is forbidden in INITIAL.

TARGET may be either performance actor, a non-animated person, a physical object/location, or UP, DOWN, LEFT, RIGHT, UP_LEFT, UP_RIGHT, DOWN_LEFT, DOWN_RIGHT. Use one identifier without spaces, such as FRONT_DOOR.

For each actor independently, consider speaking and listening behavior. Listener reactions may occur at the earliest semantically sufficient heard cue; do not automatically wait for a sentence or turn to end.

Track each actor's current persistent focus silently. Use `focus: TARGET` when attention settles from person to object, object to person, one person to another, or onto a newly revealed/inspected target. Use `eye_action: brief_check TARGET` for a temporary reaction, eye-contact avoidance, hesitation, memory/search, checking another person's reaction, or briefly checking an object without abandoning current social focus.

For internal memory/search/imagery, `eye_action: brief_check UP` or an upward diagonal can be appropriate; for guilt, shame, embarrassment, discomfort, or eye-contact avoidance, `eye_action: brief_check DOWN` or a downward diagonal can be appropriate. These are acting priors, not fixed psychological codes.

Sparsity means avoiding redundant or unmotivated changes, not minimizing focus or eye-action decisions. There is no minimum beat count. Do not mechanically create beats because a turn starts or time passes.
