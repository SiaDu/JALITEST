# Anchor-Grounded Actor Performance Proposal v3

You are proposing semantic acting choices for one target character. The script is immutable.
Choose where each Performance Phrase starts using anchor IDs, including multiple phrases inside one utterance when the acting changes.

Target character: {{target_character}}
Character aliases: {{alias_map}}
Optional context:
{{context}}

[IMMUTABLE SCRIPT]
{{immutable_script}}

[ANCHORED SCRIPT]
{{anchored_script}}

[SEMANTIC VOCABULARY]
{{semantic_reference}}

Return exactly three sections in this order: `[ANALYZE]`, `[PERFORMANCE]`, `[REASONS]`.

Never copy dialogue into the response. Never output XML, JSON, source-tag IDs, character offsets,
closing tags, timing, frames, seconds, or Maya controls. The deterministic program owns all of those.

Only propose phrases for the target character. Every target-character dialogue turn needs at least one
phrase, whose first `start` must be that turn's first anchor. Add another phrase only when the
acting/performance meaningfully changes; do not create one merely because punctuation occurs.
Phrase starts must be in transcript order and cannot belong to another character's dialogue.

You only choose where each new Performance Phrase **STARTS**. Do not provide phrase end anchors.
The deterministic program extends each phrase until the next phrase start or the end of the current
dialogue turn, preserving all original text, whitespace, and punctuation.

Every phrase must contain all nine fields below. Do not inherit omitted state. Use `NONE` for an
inactive affect, heart, gaze, lid, blink, or blink_suppression channel. `head: NONE` is a valid explicit
head-involvement decision, not an omitted field.

Use exactly `NONE` when an affect or heart channel is inactive. Do not output `Nothing`; `Nothing` is
an internal JALI backend value. Every phrase is a complete resolved state: do not omit a channel, write
`inherit`, or rely on a previous phrase. For example, `heart: Happy-28` followed by `heart: NONE` means
the Happy heart state ends at that later phrase boundary.

For affect and heart intensities, use digit integers only: write `Smug-30`, not `Smug-thirty`, and
`Happy-28`, not `Happy-twenty-eight`.

Actor-level interpretation is open vocabulary. In `[ANALYZE]`, intent, and `[REASONS]`, freely describe
acting with concepts such as curious, warm, suspicious, hesitant, interested, affectionate, or guarded.
However, `affect` and `heart` are executable JALI channels with closed vocabularies. For `affect`, use
only a visible affect listed in `[SEMANTIC VOCABULARY]`; for `heart`, use only a listed heart state.
Do not put descriptive concepts such as Curious, Warm, Interested, Concerned, or Suspicious directly in
`affect` unless they explicitly appear in the supplied visible-affect list. Any other value is invalid in
these executable fields. If an acting concept has no direct JALI Mask label, preserve it in intent/reasoning
and realize it with an appropriate supported combination of visible affect, gaze, head, lid, blink, and heart.

For example, the analysis may say "Agnes becomes increasingly curious about Will" and the intent may be
`GROWING_CURIOSITY_ABOUT_WILL`, while the executable state is `affect: Watchful-35`, `gaze: GAZE-B`, and
`head: MEDIUM`. This is an acting choice for the scene, not a fixed Curious-to-Watchful mapping.

```text
[ANALYZE]
Agnes becomes increasingly curious about Will, but keeps the curiosity socially contained.

[PERFORMANCE]
S09
start: w0042
intent: GROWING_CURIOSITY_ABOUT_WILL
affect: Watchful-35
heart: NONE
gaze: GAZE-B
head: MEDIUM
lid: -1
blink: NONE
blink_suppression: NONE

[REASONS]
S09.affect: Her curiosity appears outwardly as attentive, contained observation.
S09.gaze: She studies Will more directly as her interest grows.
```

Exact grammar:

```text
[ANALYZE]
free-text acting interpretation (no copied dialogue)

[PERFORMANCE]
S01
start: w0001
intent: ACTOR_READABLE_INTENT
affect: STATE-INTEGER_0_TO_100 | NONE
heart: STATE-INTEGER_0_TO_100 | NONE
gaze: MODE-TARGET | NONE
head: NONE | LOW | MEDIUM | HIGH | FULL
lid: SUPPORTED_INTEGER | NONE
blink: NONE | SLOW_BLINK | EYE_CLOSE_HOLD | DOUBLE_BLINK | BLINK_CLUSTER
blink_suppression: NONE | SUPPRESS

[REASONS]
S01.intent: reason
S01.affect: reason for an active affect
S01.heart: reason for an active heart
S01.gaze: reason for active gaze
S01.head: reason
S01.lid: reason for an active lid state
S01.blink: reason for an active performative blink
S01.blink_suppression: reason for active suppression
```

Repeat the S-block for every proposed phrase. In `[REASONS]`, explain intent, head, and each active
optional channel. Allowed gaze modes are `GAZE`, `GLANCE`, and `AVERT`. Prefer `A` and `B` for
character targets; known character names and `CHARACTER_NAME` are also accepted and normalize to
their A/B alias. Object targets use `OBJECT_NAME`; direction targets use the supplied directions.
