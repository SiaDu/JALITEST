"""Canonical sparse dual Performance Plan v2 construction."""

from __future__ import annotations
from copy import deepcopy
from typing import Any
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel

SCHEMA_VERSION = "dual_performance_plan_v2"


def build_dual_performance_plan_v2(proposal: dict[str, Any], *, anchor_model: ConversationAnchorModel, sequence_id: str, proposal_path: str | None = None) -> dict[str, Any]:
    characters = list(anchor_model.aliases.values())
    defaults = {"head": "HEAD-NONE"}
    initial_states = {actor: {**defaults, **deepcopy((proposal.get("initial_states") or {}).get(actor, {}))} for actor in characters}
    initial_reasons = {actor: (proposal.get("initial_reasons") or {}).get(actor) for actor in characters}
    tracks = {actor: [] for actor in characters}
    for event in proposal.get("events", []):
        tracks[event["actor"]].append({"event_id": event["event_id"], "actor": event["actor"], "anchor_id": event["anchor_id"], "changes": deepcopy(event["changes"]), "reason": event.get("reason")})
    diagnostics = proposal.get("diagnostics", {})
    original_authored_content = {
        "characters": list(characters),
        "initial_states": deepcopy(initial_states),
        "initial_reasons": deepcopy(initial_reasons),
        "tracks": deepcopy(tracks),
    }
    return {"schema_version": SCHEMA_VERSION, "sequence_id": sequence_id, "characters": characters, "acting_interpretation": str(proposal.get("analyze") or ""), "initial_states": initial_states, "initial_reasons": initial_reasons, "tracks": tracks, "diagnostics": {"errors": list(diagnostics.get("errors", [])), "warnings": list(diagnostics.get("warnings", []))}, "provenance": {"format": "dual_sparse_anchor_semantic_v2", "source_proposal": proposal_path, "event_ids": [event["event_id"] for event in proposal.get("events", [])], "original_authored_content": original_authored_content}}
