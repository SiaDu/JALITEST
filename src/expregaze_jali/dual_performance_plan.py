"""Canonical sparse dual Performance Plan construction."""

from __future__ import annotations
from copy import deepcopy
from typing import Any
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel
from expregaze_jali.dual_authored_content import canonical_dual_authored_content

SCHEMA_VERSION = "dual_performance_plan_v2"


def normalize_persistent_noops(
    proposal: dict[str, Any], *, characters: list[str], initial_states: dict[str, dict[str, Any]],
    anchor_model: ConversationAnchorModel,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Remove canonical no-op persistent channels without changing raw proposal data."""
    events = list(proposal.get("events") or [])
    anchor_order = {anchor.anchor_id: index for index, anchor in enumerate(anchor_model.anchors)}
    normalized: dict[int, dict[str, Any] | None] = {}
    warnings: list[str] = []
    for actor in characters:
        state = {
            "affect": initial_states[actor].get("affect"),
            "gaze": initial_states[actor].get("gaze"),
            "head": initial_states[actor].get("head", "HEAD-NONE"),
        }
        actor_events = [
            (index, event) for index, event in enumerate(events)
            if isinstance(event, dict) and event.get("actor") == actor
        ]
        for index, event in sorted(actor_events, key=lambda item: (anchor_order[item[1]["anchor_id"]], item[0])):
            changes = deepcopy(event.get("changes") or {})
            removed: list[str] = []
            for channel in ("affect", "head"):
                if channel in changes:
                    if changes[channel] == state[channel]:
                        changes.pop(channel)
                        removed.append(channel)
                    else:
                        state[channel] = changes[channel]
            gaze = changes.get("gaze")
            if isinstance(gaze, str) and gaze.startswith("GAZE-"):
                if gaze == state["gaze"]:
                    changes.pop("gaze")
                    removed.append("gaze")
                else:
                    state["gaze"] = gaze
            event_id = str(event.get("event_id") or "?")
            if removed:
                warnings.append(f"{event_id}: removed no-op persistent channel(s): {', '.join(removed)}")
            if not changes:
                warnings.append(f"{event_id}: dropped after no semantic changes remained")
                normalized[index] = None
                continue
            normalized[index] = {
                "event_id": event["event_id"], "actor": actor, "anchor_id": event["anchor_id"],
                "changes": changes, "reason": event.get("reason"),
            }
    return [normalized[index] for index in range(len(events)) if normalized.get(index) is not None], warnings


def build_dual_performance_plan(proposal: dict[str, Any], *, anchor_model: ConversationAnchorModel, sequence_id: str, proposal_path: str | None = None) -> dict[str, Any]:
    characters = list(anchor_model.aliases.values())
    defaults = {"head": "HEAD-NONE"}
    initial_states = {actor: {**defaults, **deepcopy((proposal.get("initial_states") or {}).get(actor, {}))} for actor in characters}
    initial_reasons = {actor: (proposal.get("initial_reasons") or {}).get(actor) for actor in characters}
    normalized_events, normalization_warnings = normalize_persistent_noops(
        proposal, characters=characters, initial_states=initial_states, anchor_model=anchor_model,
    )
    tracks = {actor: [] for actor in characters}
    for event in normalized_events:
        tracks[event["actor"]].append(event)
    diagnostics = proposal.get("diagnostics", {})
    plan = {"schema_version": SCHEMA_VERSION, "sequence_id": sequence_id, "characters": characters, "gaze_target_candidates": list(proposal.get("gaze_target_candidates") or []), "initial_states": initial_states, "initial_reasons": initial_reasons, "tracks": tracks, "diagnostics": {"errors": list(diagnostics.get("errors", [])), "warnings": [*list(diagnostics.get("warnings", [])), *normalization_warnings]}, "provenance": {"format": "dual_sparse_anchor_semantic_v2", "source_proposal": proposal_path, "event_ids": [event["event_id"] for event in proposal.get("events", [])]}}
    plan["provenance"]["original_authored_content"] = canonical_dual_authored_content(plan)
    return plan
