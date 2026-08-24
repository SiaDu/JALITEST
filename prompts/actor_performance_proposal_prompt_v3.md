# Anchor-Grounded Actor Performance Proposal v3

You are proposing semantic acting choices for one target character. The script is immutable.
Choose phrase boundaries using anchor IDs, including multiple phrases inside one utterance when the acting changes.

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

Only propose phrases for the target character. Every target-character anchor must be covered exactly
once, in order. A phrase cannot cross a dialogue turn. Use inclusive anchor ranges.

Every phrase must contain all nine fields below. Do not inherit omitted state. Use `NONE` for an
inactive affect, heart, gaze, lid, blink, or blink_suppression channel. `head: NONE` is a valid explicit
head-involvement decision, not an omitted field.

Exact grammar:

```text
[ANALYZE]
free-text acting interpretation (no copied dialogue)

[PERFORMANCE]
S01
span: w0001-w0003
intent: ACTOR_READABLE_INTENT
affect: STATE-0_TO_100 | NONE
heart: STATE-0_TO_100 | NONE
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
character targets; object targets use `OBJECT_NAME`; direction targets use the supplied directions.
