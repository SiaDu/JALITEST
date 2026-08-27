"""Canonical sparse dual Performance Plan v2 construction."""

from __future__ import annotations
from copy import deepcopy
from typing import Any
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel

SCHEMA_VERSION = "dual_performance_plan_v2"


def build_dual_performance_plan_v2(proposal: dict[str, Any], *, anchor_model: ConversationAnchorModel, sequence_id: str, proposal_path: str | None = None) -> dict[str, Any]:
    characters = list(anchor_model.aliases.values())
    tracks = {actor: [] for actor in characters}
    for event in proposal.get("events", []):
        tracks[event["actor"]].append({"event_id": event["event_id"], "anchor_id": event["anchor_id"], "changes": deepcopy(event["changes"]), "reason": event.get("reason")})
    diagnostics = proposal.get("diagnostics", {})
    return {"schema_version": SCHEMA_VERSION, "sequence_id": sequence_id, "characters": characters, "acting_interpretation": str(proposal.get("analyze") or ""), "tracks": tracks, "diagnostics": {"errors": list(diagnostics.get("errors", [])), "warnings": list(diagnostics.get("warnings", []))}, "provenance": {"format": "dual_sparse_anchor_semantic_v2", "source_proposal": proposal_path, "event_ids": [event["event_id"] for event in proposal.get("events", [])]}}
