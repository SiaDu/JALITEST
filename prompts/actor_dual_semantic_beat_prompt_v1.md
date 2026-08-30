# Dual Semantic Beat IR v1

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
attention: hold TARGET
acting: natural-language acting interpretation

{{character_b}}
affect: MASK-INTENSITY
attention: hold TARGET
acting: natural-language acting interpretation

[BEATS]
E001
actor: {{character_a}}
trigger: w0001
acting: A direct question makes the actor briefly check the other person's reaction.
attention: brief_check {{character_b}}

E002
actor: {{character_b}}
trigger: w0002
acting: The heard disclosure raises visible anxiety without changing attention.
affect: Nervous-75
```

INITIAL requires both actors. Initial attention must be `hold TARGET`. In [BEATS], affect, attention, head, and blink are all optional. Emit only fields that actually change at that beat. A beat may contain only one changed semantic channel. Do not add affect, attention, head, or blink merely to make a beat look complete.

In later beats, use only `hold TARGET` when attention shifts and should remain there, or `brief_check TARGET` for a temporary check before returning. Omit attention when it does not meaningfully change.

Affect is a strict closed vocabulary. Never invent, paraphrase, or substitute an affect label. If the desired emotion is not present, choose the closest AVAILABLE listed Mask state. Intensities are any positive integer percentage. Initial affect must be visible and not MASK-NONE. Head and blink use only the listed executable vocabulary; blink is forbidden in INITIAL.

TARGET may be either performance actor, a non-animated person, a physical object/location, or UP, DOWN, LEFT, RIGHT, UP_LEFT, UP_RIGHT, DOWN_LEFT, DOWN_RIGHT. Use one identifier without spaces, such as FRONT_DOOR.

For each actor independently, consider speaking and listening behavior. Listener reactions may occur at the earliest semantically sufficient heard cue; do not automatically wait for a sentence or turn to end.

Track each actor's current attention silently. At meaningful beats ask what this actor is attending to now: the interlocutor, an object, another person, or internal thought. Attention-only beats are valid when attention genuinely changes. Consider direct questions or address, object reveal/handoff/inspection, returning from an object to a person, social re-engagement, checking another person's reaction, and internal thought, memory, hesitation, guilt, or discomfort.

Use `attention: hold TARGET` when the new attention should remain active. Use `attention: brief_check TARGET` for a temporary check. For internal memory/search/imagery, a brief_check UP or upward diagonal can be appropriate; for guilt, shame, embarrassment, discomfort, or eye-contact avoidance, a brief_check DOWN or downward diagonal can be appropriate. These are acting priors, not fixed psychological codes.

Sparsity means avoiding redundant or unmotivated changes, not minimizing attention decisions. There is no minimum beat count. Do not mechanically create beats because a turn starts or time passes.
