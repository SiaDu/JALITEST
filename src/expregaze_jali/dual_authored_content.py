"""Canonical animator-authorable content for dual Performance Plan v2."""

from __future__ import annotations

import json
from typing import Any


def canonical_v2_authored_content(plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize v2 semantic content while excluding runtime/provenance fields."""
    characters = [str(actor) for actor in plan.get("characters", [])]
    tracks: list[dict[str, Any]] = []
    source_tracks = plan.get("tracks") or {}
    if isinstance(source_tracks, list):
        source_events = source_tracks
    else:
        source_events = [
            {**event, "actor": actor}
            for actor in characters
            for event in (source_tracks.get(actor) or [])
        ]
    for event in source_events:
        actor = str(event.get("actor") or "")
        tracks.append({
            "actor": actor,
            "anchor_id": event.get("anchor_id"),
            "changes": dict(event.get("changes") or {}),
            "reason": event.get("reason"),
        })
    tracks.sort(key=lambda row: (row["actor"], str(row["anchor_id"]), json.dumps(row["changes"], sort_keys=True), str(row["reason"])))
    return {
        "characters": characters,
        "initial_states": {actor: dict((plan.get("initial_states") or {}).get(actor) or {}) for actor in characters},
        "initial_reasons": {actor: (plan.get("initial_reasons") or {}).get(actor) for actor in characters},
        "tracks": tracks,
    }
