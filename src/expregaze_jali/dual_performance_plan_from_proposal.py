"""Build one conversation-level dual authoring plan from shared phrase starts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from expregaze_jali.performance_plan_schema import assert_no_timing_fields
from expregaze_jali.performance_proposal_parser import DIRECTION_TARGETS, ProposalValidationError
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel, speaker_key


SCHEMA_VERSION = "dual_performance_plan_v0"


def _resolve_gaze(
    value: str, model: ConversationAnchorModel, phrase_id: str, actor_alias: str
) -> str:
    if value == "NONE":
        return value
    if value == "AVERT":
        counterpart = "B" if actor_alias == "A" else "A"
        return f"AVERT-{counterpart}"
    mode, target = value.split("-", 1)
    if target in model.aliases or target in DIRECTION_TARGETS or target.startswith("OBJECT_"):
        return value
    character = target[len("CHARACTER_"):] if target.startswith("CHARACTER_") else target
    alias = next(
        (key for key, name in model.aliases.items() if speaker_key(name) == speaker_key(character)),
        None,
    )
    if alias is None:
        raise ProposalValidationError(
            f'{phrase_id}: Unknown character gaze target "{character}"'
        )
    return f"{mode}-{alias}"


def resolve_dual_phrase_boundaries(
    proposal: dict[str, Any], anchor_model: ConversationAnchorModel
) -> list[dict[str, Any]]:
    """Validate starts and deterministically partition every dialogue turn."""
    anchors = list(anchor_model.anchors)
    by_id = {anchor.anchor_id: (index, anchor) for index, anchor in enumerate(anchors)}
    turns = {turn.turn_id: turn for turn in anchor_model.turns}
    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_index: int | None = None
    for phrase in proposal.get("phrases", []):
        phrase_id = phrase["proposal_id"]
        start_id = phrase["start_anchor"]
        if start_id not in by_id:
            raise ProposalValidationError(f"{phrase_id}: unknown anchor {start_id}")
        index, anchor = by_id[start_id]
        if start_id in seen:
            raise ProposalValidationError(f"{phrase_id}: duplicate phrase boundary {start_id}")
        if previous_index is not None and index < previous_index:
            raise ProposalValidationError(f"{phrase_id}: phrase boundaries are not in transcript order")
        seen.add(start_id)
        previous_index = index
        row = deepcopy(phrase)
        row.update({"turn_id": anchor.turn_id, "start_index": index})
        for alias in ("A", "B"):
            row["states"][alias]["gaze"] = _resolve_gaze(
                row["states"][alias]["gaze"], anchor_model, phrase_id, alias
            )
        resolved.append(row)

    for turn in anchor_model.turns:
        turn_phrases = [row for row in resolved if row["turn_id"] == turn.turn_id]
        if not turn_phrases:
            raise ProposalValidationError(f"Conversation turn {turn.turn_id} has no Performance Phrase")
        if not turn.anchors:
            raise ProposalValidationError(f"Conversation turn {turn.turn_id} has no word anchors")
        first = turn.anchors[0].anchor_id
        if turn_phrases[0]["start_anchor"] != first:
            raise ProposalValidationError(
                f"{turn.turn_id}: first Performance Phrase must start at {first}"
            )

    for index, phrase in enumerate(resolved):
        anchor = anchors[phrase["start_index"]]
        next_phrase = resolved[index + 1] if index + 1 < len(resolved) else None
        end = (
            anchors[next_phrase["start_index"]].char_start
            if next_phrase is not None and next_phrase["turn_id"] == phrase["turn_id"]
            else turns[phrase["turn_id"]].utterance_end
        )
        phrase.update({
            "char_start": anchor.char_start,
            "char_end": end,
            "text": anchor_model.script[anchor.char_start:end],
        })
        phrase.pop("start_index")
    return resolved


def _locks() -> dict[str, Any]:
    state = {key: False for key in ("affect", "heart", "gaze", "head", "lid", "blink", "blink_suppression")}
    return {"intent": False, "A": dict(state), "B": dict(state)}


def build_dual_performance_plan_from_proposal(
    proposal: dict[str, Any], *, anchor_model: ConversationAnchorModel,
    sequence_id: str, proposal_path: str | None = None,
) -> dict[str, Any]:
    phrases = resolve_dual_phrase_boundaries(proposal, anchor_model)
    output_phrases: list[dict[str, Any]] = []
    for number, phrase in enumerate(phrases, 1):
        speaker = next(
            alias for alias, name in anchor_model.aliases.items()
            if speaker_key(name) == speaker_key(next(
                turn.speaker for turn in anchor_model.turns if turn.turn_id == phrase["turn_id"]
            ))
        )
        proposal_id = phrase["proposal_id"]
        reasons = deepcopy(proposal.get("reasons", {}).get(proposal_id, {}))
        output_phrases.append({
            "phrase_id": f"P{number:02d}",
            "source_proposal_id": proposal_id,
            "speaker": speaker,
            "span": {
                "text": phrase["text"],
                "turn_id": phrase["turn_id"],
                "char_start": phrase["char_start"],
                "char_end": phrase["char_end"],
            },
            "intent": phrase["intent"],
            "states": deepcopy(phrase["states"]),
            "rationale": {
                "intent": reasons.get("intent"),
                "A": deepcopy(reasons.get("A", {})),
                "B": deepcopy(reasons.get("B", {})),
            },
            "locks": _locks(),
        })
    diagnostics = proposal.get("diagnostics", {})
    plan = {
        "schema_version": SCHEMA_VERSION,
        "sequence_id": sequence_id,
        "characters": dict(anchor_model.aliases),
        "acting_interpretation": str(proposal.get("analyze") or ""),
        "phrases": output_phrases,
        "diagnostics": {
            "errors": list(diagnostics.get("errors", [])),
            "warnings": list(diagnostics.get("warnings", [])),
        },
        "proposal_provenance": {
            "format": "dual_anchor_semantic_v1",
            "source_proposal": proposal_path,
            "aliases": dict(anchor_model.aliases),
            "phrase_ids": [phrase["proposal_id"] for phrase in phrases],
        },
    }
    assert_no_timing_fields(plan)
    return plan
