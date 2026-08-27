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
        before = speaker_key(anchor.speaker) == speaker_key(actor)
        offset = anchor.start if before else anchor.end
        tags = [_format_tag(channel, event["changes"][channel]) for channel in CHANNEL_ORDER if channel in event.get("changes", {})]
        insertions.setdefault(offset, []).extend(tags)
    text = projection.display_text
    for offset in sorted(insertions, reverse=True):
        text = text[:offset] + "".join(insertions[offset]) + text[offset:]
    initial = {**INITIAL_DEFAULTS, **((plan.get("initial_states") or {}).get(actor) or {})}
    initial_tags = "".join(
        _format_tag(channel, initial[channel])
        for channel in PERSISTENT_CHANNELS
        if initial[channel] not in {"NONE", INITIAL_DEFAULTS[channel]}
    )
    return initial_tags + text


class DualSparseScoreModel:
    """Validate and apply independent actor score editors for v2 plans."""

    def __init__(self, plan: dict[str, Any], anchor_model: ConversationAnchorModel):
        if plan.get("schema_version") != "dual_performance_plan_v2":
            raise ValueError("DualSparseScoreModel requires dual_performance_plan_v2")
        self.plan = deepcopy(plan)
        self.original_plan = deepcopy(plan)
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
            if target.upper() == "NONE":
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

    def validate_actor(self, actor: str, text: str) -> ScoreValidation:
        if actor not in self.characters:
            return ScoreValidation((), (ScoreIssue(actor, "Unknown actor editor"),))
        errors: list[ScoreIssue] = []
        placements: list[tuple[re.Match[str], int, dict[str, str]]] = []
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
                placements.append((match, clean_length, changes))
            cursor = match.end()
        stripped.append(text[cursor:])
        clean = "".join(stripped)
        edited_tokens = list(re.finditer(r"\S+", clean))
        canonical_tokens = list(self.projection.anchors)
        if [match.group(0) for match in edited_tokens] != [anchor.text for anchor in canonical_tokens]:
            errors.append(ScoreIssue(actor, "Dialogue tokens and punctuation are immutable and must preserve the canonical anchor sequence"))
        grouped: dict[str, dict[str, str]] = {}
        initial_changes: dict[str, str] = {}
        for cluster, offset, changes in placements:
            prior_indices = [index for index, token in enumerate(edited_tokens) if token.end() == offset]
            next_indices = [index for index, token in enumerate(edited_tokens) if token.start() == offset]
            embedded = any(token.start() < offset < token.end() for token in edited_tokens)
            before_first_token = not edited_tokens or clean[:offset].strip() == ""
            direct_previous = cluster.start() > 0 and not text[cluster.start() - 1].isspace()
            direct_next = cluster.end() < len(text) and not text[cluster.end()].isspace()
            placement_key: str | None = None
            if before_first_token:
                if "blink" in changes:
                    errors.append(ScoreIssue(actor, "Initial state tag cluster cannot contain blink"))
                    changes = {key: value for key, value in changes.items() if key != "blink"}
                if str(changes.get("gaze", "")).startswith("GLANCE-"):
                    errors.append(ScoreIssue(actor, "Initial gaze must be persistent GAZE, not GLANCE"))
                    changes = {key: value for key, value in changes.items() if key != "gaze"}
                placement_key = "__INITIAL__"
            elif embedded:
                errors.append(ScoreIssue(actor, "Tag cluster cannot split a dialogue token"))
            elif direct_next and next_indices and next_indices[0] < len(canonical_tokens) and speaker_key(canonical_tokens[next_indices[0]].speaker) == speaker_key(actor):
                placement_key = canonical_tokens[next_indices[0]].anchor_id
            elif direct_previous and prior_indices and prior_indices[-1] < len(canonical_tokens) and speaker_key(canonical_tokens[prior_indices[-1]].speaker) != speaker_key(actor):
                placement_key = canonical_tokens[prior_indices[-1]].anchor_id
            elif not direct_previous and not direct_next:
                errors.append(ScoreIssue(actor, "Tag cluster placement is ambiguous; attach it directly before an own spoken token or after a heard token"))
            else:
                errors.append(ScoreIssue(actor, "Tag cluster is not role-appropriately before an own spoken token or after a heard token"))
            if placement_key is None or not changes:
                continue
            destination = initial_changes if placement_key == "__INITIAL__" else grouped.setdefault(placement_key, {})
            for channel, value in changes.items():
                if channel in destination:
                    errors.append(ScoreIssue(actor, f"Duplicate {channel} change at {'initial state' if placement_key == '__INITIAL__' else placement_key}"))
                destination[channel] = value
        events = tuple({"actor": actor, "anchor_id": anchor_id, "changes": changes} for anchor_id, changes in grouped.items())
        if "affect" not in initial_changes:
            errors.append(ScoreIssue(actor, "Initial performance must include a visible affect tag"))
        elif initial_changes["affect"] == "MASK-NONE":
            errors.append(ScoreIssue(actor, "Initial affect cannot be MASK-NONE"))
        if "gaze" not in initial_changes:
            errors.append(ScoreIssue(actor, "Initial performance must include a persistent GAZE tag"))
        if not str(self.plan.get("initial_reasons", {}).get(actor) or "").strip():
            errors.append(ScoreIssue(actor, "Initial performance requires a meaningful reason"))
        if initial_changes:
            events = ({"actor": actor, "initial": True, "changes": initial_changes}, *events)
        return ScoreValidation(events, tuple(errors))

    def validate(self, value: str | dict[str, str]) -> ScoreValidation:
        texts = value if isinstance(value, dict) else {self.characters[0]: value, self.characters[1]: self.score_texts[self.characters[1]]}
        results = [self.validate_actor(actor, texts.get(actor, "")) for actor in self.characters]
        return ScoreValidation(tuple(event for result in results for event in result.events), tuple(error for result in results for error in result.errors))

    def apply(self, value: str | dict[str, str]) -> dict[str, Any]:
        texts = value if isinstance(value, dict) else {self.characters[0]: value, self.characters[1]: self.score_texts[self.characters[1]]}
        result = self.validate(texts)
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
            original_changes = deepcopy((original or prior or {}).get("original_changes", (original or prior or {}).get("changes") or {}))
            semantic_changed = original is None or changes != original_changes
            prior_status = (prior or {}).get("reason_status")
            prior_reason = (prior or {}).get("reason")
            original_reason = (original or prior or {}).get("original_reason", (original or prior or {}).get("reason"))
            reason_changed = prior is not None and str(prior_reason or "") != str(original_reason or "")
            changed = semantic_changed or reason_changed
            is_same_pending_edit = bool(
                changed and prior and prior.get("changes") == changes
                and prior_status in {"user_confirmed", "user_edited"}
            )
            tracks[row["actor"]].append({
                "event_id": event_id, "actor": row["actor"], "source_event_id": (original or prior or {}).get("source_event_id", event_id),
                "anchor_id": row["anchor_id"], "changes": changes,
                "original_changes": original_changes if original is not None else None,
                "reason": prior.get("reason") if prior else None,
                "original_reason": original_reason,
                "edited_by_user": changed,
                "reason_status": prior_status if is_same_pending_edit else ("needs_confirmation" if semantic_changed else ("user_edited" if reason_changed else "llm_original")),
            })
        self.plan["tracks"] = tracks
        self.plan["initial_states"] = initial_states
        self._refresh_initial_provenance()
        self.score_texts = dict(texts)
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
            status = old.get("reason_status")
            reason_changed = str(old.get("reason", self.plan["initial_reasons"].get(actor)) or "") != str(old.get("original_reason", (self.original_plan.get("initial_reasons") or {}).get(actor)) or "")
            changed = semantic_changed or reason_changed
            retain_resolution = changed and status in {"user_confirmed", "user_edited"}
            rows[actor] = {
                "source_event_id": f"INITIAL:{actor}",
                "original_state": original_state,
                "original_reason": old.get("original_reason", (self.original_plan.get("initial_reasons") or {}).get(actor)),
                "reason": old.get("reason", self.plan["initial_reasons"].get(actor)),
                "edited_by_user": changed,
                "reason_status": status if retain_resolution else ("needs_confirmation" if semantic_changed else ("user_edited" if reason_changed else "llm_original")),
            }
            self.plan["initial_reasons"][actor] = rows[actor]["reason"]
        self.plan["initial_provenance"] = rows

    def _provenance_row(self, actor: str, event_id: str) -> dict[str, Any]:
        if actor not in self.characters:
            raise ValueError(f"Unknown actor: {actor}")
        if event_id == f"INITIAL:{actor}":
            return self.plan["initial_provenance"][actor]
        for event in self.plan["tracks"][actor]:
            if event.get("event_id") == event_id:
                return event
        raise ValueError(f"Unknown event for {actor}: {event_id}")

    def confirm_reason(self, actor: str, event_id: str) -> None:
        """Explicitly retain the original LLM reason for a changed decision."""
        row = self._provenance_row(actor, event_id)
        if row.get("reason_status") != "needs_confirmation":
            raise ValueError("Only a changed decision requiring confirmation can confirm its reason")
        original_reason = str(row.get("original_reason") or "").strip()
        if not original_reason:
            raise ValueError("A newly added decision has no original LLM reason; write an animator reason")
        row["reason"] = original_reason
        row["reason_status"] = "user_confirmed"
        if event_id == f"INITIAL:{actor}":
            self.plan["initial_reasons"][actor] = original_reason

    def set_reason(self, actor: str, event_id: str, reason: str) -> None:
        """Set an animator-authored rationale for an edited or newly added decision."""
        clean = str(reason).strip()
        if not clean:
            raise ValueError("Animator reason cannot be empty")
        row = self._provenance_row(actor, event_id)
        row["reason"] = clean
        row["reason_status"] = "user_edited"
        if event_id == f"INITIAL:{actor}":
            self.plan["initial_reasons"][actor] = clean

    def rationale_view(self, event_number: int) -> str:
        rows = self.reason_entries()
        if not rows:
            return "No sparse change events have reasons."
        actor, event = rows[max(0, min(event_number - 1, len(rows) - 1))]
        if event.get("initial"):
            original = event.get("original_reason")
            if event.get("reason_status") in {"needs_confirmation", "user_confirmed", "user_edited"}:
                return f'{actor} INITIAL\n{event["changes"]}\n\nOriginal LLM reason:\n{original or "(none)"}\n\nCurrent / Animator reason ({event.get("reason_status")}):\n{event.get("reason") or "(needs confirmation)"}'
            return f'{actor} INITIAL\n{event["changes"]}\n\nReason:\n{event.get("reason") or "(none)"}'
        anchor = self.projection.anchor_map[event["anchor_id"]]
        changes = "\n".join(f"{channel} -> {value}" for channel, value in event["changes"].items())
        original = event.get("original_reason")
        if event.get("reason_status") in {"needs_confirmation", "user_confirmed", "user_edited"}:
            return f'{actor} @ "{anchor.text}"\n{changes}\n\nOriginal LLM reason:\n{original or "(none)"}\n\nCurrent / Animator reason ({event.get("reason_status")}):\n{event.get("reason") or "(needs confirmation)"}'
        return f'{actor} @ "{anchor.text}"\n{changes}\n\nReason:\n{event["reason"]}'

    def reason_entries(self) -> list[tuple[str, dict[str, Any]]]:
        """Return authored sparse decisions, including those awaiting a reason."""
        initial = [
            (actor, {**self.plan["initial_provenance"][actor], "event_id": f"INITIAL:{actor}", "initial": True,
                     "changes": self.plan["initial_states"][actor]})
            for actor in self.characters
        ]
        return initial + [(actor, event) for actor in self.characters for event in self.plan["tracks"][actor]]
