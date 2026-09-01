"""Pure-Python editable score projection for dual Performance Plan v2."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any

from expregaze_jali.dual_sparse_performance_proposal_parser import BLINK_VALUES, HEAD_VALUES
from expregaze_jali.performance_proposal_parser import load_semantic_vocabulary
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel, speaker_key

CHANNEL_ORDER = ("affect", "gaze", "head", "blink")
PERSISTENT_CHANNELS = ("affect", "gaze", "head")
INITIAL_DEFAULTS = {"affect": "MASK-NONE", "gaze": "__NEUTRAL__", "head": "HEAD-NONE"}
_TAG = re.compile(r"<([^<>\r\n]+)>")
_TAG_CLUSTER = re.compile(r"(?:<[^<>\r\n]+>)+")
_AFFECT = re.compile(r"^(.+)-(\d+)$")
_GAZE = re.compile(r"^(GAZE|GLANCE)-(.+)$")


def _authored_content_snapshot(plan: dict[str, Any]) -> dict[str, Any]:
    """Preserve the original LLM-authored fields across numbered snapshots."""
    characters = list(plan.get("characters") or [])
    return {
        "characters": characters,
        "initial_states": deepcopy(plan.get("initial_states") or {}),
        "initial_reasons": deepcopy(plan.get("initial_reasons") or {}),
        "tracks": {actor: deepcopy((plan.get("tracks") or {}).get(actor) or []) for actor in characters},
    }


@dataclass(frozen=True)
class DisplayAnchor:
    anchor_id: str
    text: str
    speaker: str
    start: int
    end: int


@dataclass(frozen=True)
class SpeakerRange:
    speaker: str
    start: int
    end: int


@dataclass(frozen=True)
class DialogueProjection:
    display_text: str
    anchors: tuple[DisplayAnchor, ...]
    speaker_ranges: tuple[SpeakerRange, ...]

    @property
    def anchor_map(self) -> dict[str, DisplayAnchor]:
        return {anchor.anchor_id: anchor for anchor in self.anchors}


@dataclass(frozen=True)
class ScoreIssue:
    actor: str
    message: str

    def __str__(self) -> str:
        return f"{self.actor}: {self.message}"


@dataclass(frozen=True)
class ScoreValidation:
    events: tuple[dict[str, Any], ...]
    errors: tuple[ScoreIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def phrases(self) -> tuple[dict[str, Any], ...]:
        return self.events


def build_dialogue_projection(model: ConversationAnchorModel) -> DialogueProjection:
    """Project immutable dialogue without speaker prefixes."""
    chunks: list[str] = []
    anchors: list[DisplayAnchor] = []
    ranges: list[SpeakerRange] = []
    offset = 0
    for turn_index, turn in enumerate(model.turns):
        if turn_index:
            chunks.append("\n")
            offset += 1
        utterance = turn.utterance_text
        turn_start = offset
        chunks.append(utterance)
        for anchor in turn.anchors:
            relative_start = anchor.char_start - turn.utterance_start
            relative_end = anchor.char_end - turn.utterance_start
            anchors.append(DisplayAnchor(anchor.anchor_id, anchor.text, turn.speaker, offset + relative_start, offset + relative_end))
        offset += len(utterance)
        ranges.append(SpeakerRange(turn.speaker, turn_start, offset))
    return DialogueProjection("".join(chunks), tuple(anchors), tuple(ranges))


def _format_tag(channel: str, value: str) -> str:
    del channel
    return f"<{value}>"


def render_actor_score(plan: dict[str, Any], projection: DialogueProjection, actor: str) -> str:
    insertions: dict[int, list[str]] = {}
    anchors = projection.anchor_map
    for event in plan.get("tracks", {}).get(actor, []):
        anchor = anchors[event["anchor_id"]]
        offset = anchor.start
        tags = [_format_tag(channel, event["changes"][channel]) for channel in CHANNEL_ORDER if channel in event.get("changes", {})]
        insertions.setdefault(offset, []).extend(tags)
    text = projection.display_text
    for offset in sorted(insertions, reverse=True):
        text = text[:offset] + "".join(insertions[offset]) + text[offset:]
    return text


def render_initial_score(plan: dict[str, Any], actor: str) -> str:
    initial = {**INITIAL_DEFAULTS, **((plan.get("initial_states") or {}).get(actor) or {})}
    return "".join(
        _format_tag(channel, initial[channel])
        for channel in PERSISTENT_CHANNELS
        if initial[channel] not in {"NONE", INITIAL_DEFAULTS[channel]}
    )


def projection_offset_from_score_plain_offset(score_text: str, plain_offset: int) -> int:
    """Dialogue score offsets map directly to the canonical projection."""
    del score_text
    return plain_offset


def resolve_tag_offset_to_anchor(projection: DialogueProjection, clean_offset: int) -> DisplayAnchor:
    """Resolve a tag's offset in tag-free text to the nearest dialogue anchor.

    The actor panel determines who owns the resulting event.  Physical tag
    placement only chooses an anchor in the shared dialogue projection.
    """
    anchors = projection.anchors
    if not anchors:
        raise ValueError("Cannot place a semantic tag without dialogue anchors")

    offset = int(clean_offset)
    if offset <= anchors[0].start:
        return anchors[0]
    if offset >= anchors[-1].end:
        return anchors[-1]

    # Anchor ends are deliberately inclusive for editing: a tag immediately
    # after a token still belongs to that token.
    for anchor in anchors:
        if anchor.start <= offset <= anchor.end:
            return anchor

    for previous, following in zip(anchors, anchors[1:]):
        if previous.end < offset < following.start:
            previous_distance = offset - previous.end
            following_distance = following.start - offset
            # A tie resolves forward so whitespace placement is deterministic.
            return following if following_distance <= previous_distance else previous

    # Conversation anchors are ordered and non-overlapping.  This defensive
    # fallback keeps the helper deterministic for a malformed projection.
    return min(anchors, key=lambda anchor: (min(abs(offset - anchor.start), abs(offset - anchor.end)), -anchor.start))


def _canonical_offset_from_tag_free_text(
    clean_offset: int,
    edited_tokens: list[re.Match[str]],
    canonical_tokens: list[DisplayAnchor],
) -> int:
    """Map an editable score offset into the canonical dialogue projection.

    Whitespace is intentionally editable, so its raw offsets can differ from
    the projection.  Token-relative positions remain exact; a whitespace gap
    resolves against its adjacent token boundaries, with the same forward tie
    break used by ``resolve_tag_offset_to_anchor``.
    """
    if not canonical_tokens:
        return clean_offset
    if not edited_tokens:
        return canonical_tokens[0].start
    for index, token in enumerate(edited_tokens):
        if token.start() <= clean_offset <= token.end() and index < len(canonical_tokens):
            anchor = canonical_tokens[index]
            return anchor.start + min(clean_offset - token.start(), anchor.end - anchor.start)
    if clean_offset <= edited_tokens[0].start():
        return canonical_tokens[0].start
    if clean_offset >= edited_tokens[-1].end():
        return canonical_tokens[-1].end
    for index, (previous, following) in enumerate(zip(edited_tokens, edited_tokens[1:])):
        if previous.end() < clean_offset < following.start():
            previous_distance = clean_offset - previous.end()
            following_distance = following.start() - clean_offset
            previous_anchor = canonical_tokens[min(index, len(canonical_tokens) - 1)]
            following_anchor = canonical_tokens[min(index + 1, len(canonical_tokens) - 1)]
            return following_anchor.start if following_distance <= previous_distance else previous_anchor.end
    return clean_offset


class DualSparseScoreModel:
    """Validate and apply independent actor score editors for v2 plans."""

    def __init__(self, plan: dict[str, Any], anchor_model: ConversationAnchorModel):
        if plan.get("schema_version") != "dual_performance_plan_v2":
            raise ValueError("DualSparseScoreModel requires dual_performance_plan_v2")
        self.plan = deepcopy(plan)
        self.original_plan = deepcopy(plan)
        provenance = self.plan.setdefault("provenance", {})
        provenance.setdefault("original_authored_content", _authored_content_snapshot(plan))
        self.original_plan.setdefault("provenance", {})["original_authored_content"] = deepcopy(provenance["original_authored_content"])
        self.characters = list(self.plan.get("characters", []))
        if len(self.characters) != 2 or set(self.plan.get("tracks", {})) != set(self.characters):
            raise ValueError("v2 requires two named characters and name-keyed tracks")
        self.plan["initial_states"] = {
            actor: {**INITIAL_DEFAULTS, **((self.plan.get("initial_states") or {}).get(actor) or {})}
            for actor in self.characters
        }
        self.plan["initial_reasons"] = {
            actor: (self.plan.get("initial_reasons") or {}).get(actor)
            for actor in self.characters
        }
        stored_initial_provenance = self.plan.get("initial_provenance") or {}
        self.plan["initial_provenance"] = {
            actor: {
                "source_event_id": (stored_initial_provenance.get(actor) or {}).get("source_event_id", f"INITIAL:{actor}"),
                "original_state": deepcopy((stored_initial_provenance.get(actor) or {}).get("original_state", (self.original_plan.get("initial_states") or {}).get(actor) or {})),
                "original_reason": (stored_initial_provenance.get(actor) or {}).get("original_reason", (self.original_plan.get("initial_reasons") or {}).get(actor)),
                "reason": (stored_initial_provenance.get(actor) or {}).get("reason", (self.plan.get("initial_reasons") or {}).get(actor)),
                "edited_by_user": bool((stored_initial_provenance.get(actor) or {}).get("edited_by_user", False)),
                "reason_status": (stored_initial_provenance.get(actor) or {}).get("reason_status", "llm_original"),
            }
            for actor in self.characters
        }
        self.projection = build_dialogue_projection(anchor_model)
        self.score_texts = {actor: render_actor_score(self.plan, self.projection, actor) for actor in self.characters}
        self.initial_score_texts = {actor: render_initial_score(self.plan, actor) for actor in self.characters}
        self.score_text = self.score_texts[self.characters[0]]
        self.phrases = [event for actor in self.characters for event in self.plan["tracks"][actor]]
        vocabulary = load_semantic_vocabulary()
        self.affect_states = {value.lower(): value for value in vocabulary.affect_states.values()}
        self.targets: set[str] = set()

    def _tag(self, actor: str, value: str) -> tuple[str, str] | str:
        upper = value.upper()
        if upper == "MASK-NONE":
            return "affect", "MASK-NONE"
        match = _AFFECT.fullmatch(value)
        if match and match.group(1).lower() in self.affect_states and int(match.group(2)) > 0:
            return "affect", f"{self.affect_states[match.group(1).lower()]}-{int(match.group(2))}"
        gaze = _GAZE.fullmatch(value)
        if gaze:
            mode, target = gaze.group(1).upper(), gaze.group(2)
            if target.upper() in {"NONE", "TARGET"}:
                return f'Unknown or invalid v2 tag <{value}>'
            directions = {"RIGHT", "LEFT", "DOWN", "DOWN_LEFT", "DOWN_RIGHT", "UP", "UP_LEFT", "UP_RIGHT"}
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_'-]*", target):
                named = next((name for name in self.characters if speaker_key(name) == speaker_key(target)), target)
                return "gaze", f"{mode}-{named if target.upper() not in directions else target.upper()}"
        if upper in HEAD_VALUES:
            return "head", upper
        if upper in BLINK_VALUES:
            return "blink", upper
        return f'Unknown or invalid v2 tag <{value}>'

    def validate_initial_actor(self, actor: str, text: str) -> tuple[dict[str, str], list[ScoreIssue]]:
        errors: list[ScoreIssue] = []
        cluster = text.strip()
        changes: dict[str, str] = {}
        if not _TAG_CLUSTER.fullmatch(cluster):
            errors.append(ScoreIssue(actor, "Initial performance must contain only semantic tags"))
            return changes, errors
        for tag in _TAG.finditer(cluster):
            parsed = self._tag(actor, tag.group(1).strip())
            if isinstance(parsed, str):
                errors.append(ScoreIssue(actor, parsed)); continue
            channel, value = parsed
            if channel in changes:
                errors.append(ScoreIssue(actor, f"Duplicate initial {channel} tag"))
            changes[channel] = value
        if "blink" in changes:
            errors.append(ScoreIssue(actor, "Initial state tag cluster cannot contain blink"))
        if str(changes.get("gaze", "")).startswith("GLANCE-"):
            errors.append(ScoreIssue(actor, "Initial gaze must be persistent GAZE, not GLANCE"))
        if "affect" not in changes:
            errors.append(ScoreIssue(actor, "Initial performance must include a visible affect tag"))
        elif changes["affect"] == "MASK-NONE":
            errors.append(ScoreIssue(actor, "Initial affect cannot be MASK-NONE"))
        if "gaze" not in changes:
            errors.append(ScoreIssue(actor, "Initial performance must include a persistent GAZE tag"))
        return changes, errors

    def validate_actor(self, actor: str, text: str, initial_text: str | None = None) -> ScoreValidation:
        if actor not in self.characters:
            return ScoreValidation((), (ScoreIssue(actor, "Unknown actor editor"),))
        initial_changes, errors = self.validate_initial_actor(actor, initial_text if initial_text is not None else self.initial_score_texts[actor])
        placements: list[tuple[int, dict[str, str]]] = []
        stripped: list[str] = []
        cursor = 0
        clean_length = 0
        for match in _TAG_CLUSTER.finditer(text):
            plain = text[cursor:match.start()]
            stripped.append(plain)
            clean_length += len(plain)
            changes: dict[str, str] = {}
            for tag in _TAG.finditer(match.group(0)):
                parsed = self._tag(actor, tag.group(1).strip())
                if isinstance(parsed, str):
                    errors.append(ScoreIssue(actor, parsed))
                    continue
                channel, value = parsed
                if channel in changes:
                    errors.append(ScoreIssue(actor, f"Duplicate {channel} change in one tag cluster"))
                changes[channel] = value
            if changes:
                placements.append((clean_length, changes))
            cursor = match.end()
        stripped.append(text[cursor:])
        clean = "".join(stripped)
        edited_tokens = list(re.finditer(r"\S+", clean))
        canonical_tokens = list(self.projection.anchors)
        if [match.group(0) for match in edited_tokens] != [anchor.text for anchor in canonical_tokens]:
            errors.append(ScoreIssue(actor, "Dialogue tokens and punctuation are immutable and must preserve the canonical anchor sequence"))
        grouped: dict[str, dict[str, str]] = {}
        for offset, changes in placements:
            try:
                canonical_offset = _canonical_offset_from_tag_free_text(offset, edited_tokens, canonical_tokens)
                anchor = resolve_tag_offset_to_anchor(self.projection, canonical_offset)
            except ValueError as exc:
                errors.append(ScoreIssue(actor, str(exc)))
                continue
            destination = grouped.setdefault(anchor.anchor_id, {})
            for channel, value in changes.items():
                if channel in destination:
                    errors.append(ScoreIssue(actor, f"Duplicate {channel} change at {anchor.anchor_id}"))
                destination[channel] = value
        events = tuple({"actor": actor, "anchor_id": anchor_id, "changes": changes} for anchor_id, changes in grouped.items())
        if initial_changes:
            events = ({"actor": actor, "initial": True, "changes": initial_changes}, *events)
        hold_active = False
        for event in events:
            blink = event["changes"].get("blink")
            if blink == "EYE_CLOSE_HOLD":
                if hold_active:
                    errors.append(ScoreIssue(actor, "EYE_CLOSE_HOLD cannot occur while an authored eye hold is active"))
                hold_active = True
            elif blink == "EYE_OPEN":
                if not hold_active:
                    errors.append(ScoreIssue(actor, "EYE_OPEN requires an active authored EYE_CLOSE_HOLD"))
                hold_active = False
            elif blink in {"SLOW_BLINK", "DOUBLE_BLINK"} and hold_active:
                errors.append(ScoreIssue(actor, f"{blink} is invalid while an authored eye hold is active"))
        return ScoreValidation(events, tuple(errors))

    def _actor_payload(self, value: str | dict[str, Any], actor: str) -> tuple[str, str]:
        if not isinstance(value, dict):
            return (str(value) if actor == self.characters[0] else self.score_texts[actor], self.initial_score_texts[actor])
        row = value.get(actor)
        if isinstance(row, dict):
            return str(row.get("dialogue", "")), str(row.get("initial", ""))
        return str(row if row is not None else self.score_texts[actor]), self.initial_score_texts[actor]

    def validate(self, value: str | dict[str, Any]) -> ScoreValidation:
        results = [self.validate_actor(actor, *self._actor_payload(value, actor)) for actor in self.characters]
        return ScoreValidation(tuple(event for result in results for event in result.events), tuple(error for result in results for error in result.errors))

    def apply(self, value: str | dict[str, Any]) -> dict[str, Any]:
        payloads = {actor: self._actor_payload(value, actor) for actor in self.characters}
        result = self.validate(value)
        if not result.valid:
            raise ValueError("\n".join(str(error) for error in result.errors))
        old = {(actor, event["anchor_id"]): event for actor in self.characters for event in self.plan["tracks"][actor]}
        originals = {(actor, event["anchor_id"]): event for actor in self.characters for event in self.original_plan["tracks"][actor]}
        next_id = max([int(event["event_id"][1:]) for event in old.values() if re.fullmatch(r"E\d+", event.get("event_id", ""))] or [0]) + 1
        tracks = {actor: [] for actor in self.characters}
        initial_states = {actor: dict(INITIAL_DEFAULTS) for actor in self.characters}
        for row in result.events:
            if row.get("initial"):
                initial_states[row["actor"]].update(deepcopy(row["changes"]))
                continue
            prior = old.get((row["actor"], row["anchor_id"]))
            original = originals.get((row["actor"], row["anchor_id"]))
            event_id = prior.get("event_id") if prior else f"E{next_id:03d}"
            if prior is None:
                next_id += 1
            changes = deepcopy(row["changes"])
            source = original or prior
            user_added = original is None and (prior is None or prior.get("reason_status") == "user_added_no_reason")
            original_changes = deepcopy(source.get("original_changes", source.get("changes") or {}) if source else {})
            semantic_changed = original is None or changes != original_changes
            original_reason = source.get("original_reason", source.get("reason")) if source else None
            tracks[row["actor"]].append({
                "event_id": event_id, "actor": row["actor"], "source_event_id": source.get("source_event_id", event_id) if source else event_id,
                "anchor_id": row["anchor_id"], "changes": changes,
                "original_changes": original_changes if original is not None else None,
                "reason": original_reason,
                "original_reason": original_reason,
                "edited_by_user": semantic_changed,
                "reason_status": "user_added_no_reason" if user_added else ("stale_after_user_edit" if semantic_changed else "llm_original"),
            })
        self.plan["tracks"] = tracks
        self.plan["initial_states"] = initial_states
        self._refresh_initial_provenance()
        self.score_texts = {actor: payloads[actor][0] for actor in self.characters}
        self.initial_score_texts = {actor: payloads[actor][1] for actor in self.characters}
        self.score_text = self.score_texts[self.characters[0]]
        self.phrases = [event for actor in self.characters for event in tracks[actor]]
        return self.plan

    def _refresh_initial_provenance(self) -> None:
        """Track initial-state edits separately from sparse anchor events."""
        prior = self.plan.get("initial_provenance") or {}
        rows: dict[str, dict[str, Any]] = {}
        for actor in self.characters:
            old = prior.get(actor) or {}
            original_state = deepcopy(old.get("original_state", (self.original_plan.get("initial_states") or {}).get(actor) or {}))
            semantic_changed = self.plan["initial_states"][actor] != {**INITIAL_DEFAULTS, **original_state}
            original_reason = old.get("original_reason", (self.original_plan.get("initial_reasons") or {}).get(actor))
            rows[actor] = {
                "source_event_id": f"INITIAL:{actor}",
                "original_state": original_state,
                "original_reason": original_reason,
                "reason": original_reason,
                "edited_by_user": semantic_changed,
                "reason_status": "stale_after_user_edit" if semantic_changed else "llm_original",
            }
            self.plan["initial_reasons"][actor] = rows[actor]["reason"]
        self.plan["initial_provenance"] = rows

    def rationale_view(self, event_number: int) -> str:
        rows = self.reason_entries()
        if not rows:
            return "No sparse change events have reasons."
        actor, event = rows[max(0, min(event_number - 1, len(rows) - 1))]
        reason = event.get("reason")
        if event.get("reason_status") == "user_added_no_reason":
            rationale = "No original acting interpretation — user-added semantic change."
        else:
            rationale = str(reason or "(none)")
            if event.get("reason_status") == "stale_after_user_edit" and reason:
                rationale += "\n\nOriginal rationale — semantic tag has been edited."
        if event.get("initial"):
            return f'{actor} INITIAL\n{event["changes"]}\n\nRationale:\n{rationale}'
        anchor = self.projection.anchor_map[event["anchor_id"]]
        changes = "\n".join(f"{channel} -> {value}" for channel, value in event["changes"].items())
        return f'{actor} @ "{anchor.text}"\n{changes}\n\nRationale:\n{rationale}'

    def reason_entries(self) -> list[tuple[str, dict[str, Any]]]:
        """Return authored sparse decisions for the read-only reason display."""
        initial = [
            (actor, {**self.plan["initial_provenance"][actor], "event_id": f"INITIAL:{actor}", "initial": True,
                     "changes": self.plan["initial_states"][actor]})
            for actor in self.characters
        ]
        return initial + [(actor, event) for actor in self.characters for event in self.plan["tracks"][actor]]
