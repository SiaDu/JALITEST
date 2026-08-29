"""Canonical sparse dual Performance Plan v2 construction."""

from __future__ import annotations
from copy import deepcopy
from typing import Any
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel
from expregaze_jali.dual_v2_authored_content import canonical_v2_authored_content

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
    plan = {"schema_version": SCHEMA_VERSION, "sequence_id": sequence_id, "characters": characters, "gaze_target_candidates": list(proposal.get("gaze_target_candidates") or []), "initial_states": initial_states, "initial_reasons": initial_reasons, "tracks": tracks, "diagnostics": {"errors": list(diagnostics.get("errors", [])), "warnings": list(diagnostics.get("warnings", []))}, "provenance": {"format": "dual_sparse_anchor_semantic_v2", "source_proposal": proposal_path, "event_ids": [event["event_id"] for event in proposal.get("events", [])]}}
    plan["provenance"]["original_authored_content"] = canonical_v2_authored_content(plan)
    return plan
