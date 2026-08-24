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
S01.intent: reason for the shared conversational beat
S01.A.affect: reason for A's active affect
S01.A.gaze: reason for A's active gaze
S01.A.head: reason for A's head choice
S01.B.affect: reason for B's active affect
S01.B.gaze: reason for B's active gaze
S01.B.head: reason for B's head choice
```

Repeat the S block for every shared phrase. Explain intent, head, and every active optional channel.
Allowed gaze modes are `GAZE`, `GLANCE`, and `AVERT`. Use A/B for character targets; known character
names and `CHARACTER_NAME` are accepted. Objects use `OBJECT_NAME`; directions use supplied values.
