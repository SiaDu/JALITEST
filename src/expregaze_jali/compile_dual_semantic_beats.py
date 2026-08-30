"""Deterministically compile Semantic Beat attention into v2 executable changes."""

from __future__ import annotations

from typing import Any

from expregaze_jali.dual_sparse_performance_proposal_parser import DIRECTION_TARGETS
from expregaze_jali.performance_proposal_parser import ProposalValidationError
from expregaze_jali.transcript_anchor_model import ConversationAnchorModel, speaker_key


def _gaze(action: dict[str, str]) -> str:
    return ("GAZE-" if action["action"] == "hold" else "GLANCE-") + action["target"]


def compile_dual_semantic_beats(semantic_ir: dict[str, Any], *, anchor_model: ConversationAnchorModel) -> dict[str, Any]:
    characters = tuple(anchor_model.aliases.values())
    initial_states: dict[str, dict[str, str]] = {}
    initial_reasons: dict[str, str] = {}
    persistent: dict[str, str] = {}
    calibration: list[str] = []
    warnings = list((semantic_ir.get("diagnostics") or {}).get("warnings") or [])

    def record_target(target: str) -> None:
        if target.upper() in DIRECTION_TARGETS or any(speaker_key(target) == speaker_key(name) for name in characters):
            return
        if target not in calibration:
            calibration.append(target)
        if len(calibration) > 5:
            raise ProposalValidationError("Semantic Beat IR contains more than five calibration attention targets.")

    for actor in characters:
        row = semantic_ir["initial"][actor]
        attention = row["attention"]
        if attention["action"] != "hold":
            raise ProposalValidationError(f"{actor} initial: attention must be hold TARGET")
        gaze = _gaze(attention)
        initial_states[actor] = {"affect": row["affect"], "gaze": gaze, "head": row.get("head", "HEAD-NONE")}
        initial_reasons[actor] = row["acting"]
        persistent[actor] = attention["target"]
        record_target(attention["target"])
    anchor_order = {anchor.anchor_id: index for index, anchor in enumerate(anchor_model.anchors)}
    tracks = {actor: [] for actor in characters}
    indexed = list(enumerate(semantic_ir.get("beats") or []))
    for _index, beat in sorted(indexed, key=lambda row: (anchor_order[row[1]["anchor_id"]], row[0])):
        actor = beat["actor"]; changes: dict[str, str] = {}
        for channel in ("affect", "head", "blink"):
            if channel in beat: changes[channel] = beat[channel]
        attention = beat.get("attention")
        if attention:
            record_target(attention["target"])
            if attention["action"] == "brief_check":
                changes["gaze"] = _gaze(attention)
            elif attention["target"] != persistent[actor]:
                changes["gaze"] = _gaze(attention)
                persistent[actor] = attention["target"]
            else:
                warnings.append(f"{beat['event_id']}: removed no-op attention hold")
        if not changes:
            warnings.append(f"{beat['event_id']}: dropped after no semantic changes remained")
            continue
        tracks[actor].append({"event_id": beat["event_id"], "actor": actor, "anchor_id": beat["anchor_id"], "changes": changes, "reason": beat["acting"]})
    return {"gaze_target_candidates": calibration, "initial_states": initial_states, "initial_reasons": initial_reasons, "events": [event for actor in characters for event in tracks[actor]], "diagnostics": {"errors": [], "warnings": warnings}}


def render_compiled_dual_performance_proposal(proposal: dict[str, Any], *, characters: tuple[str, str]) -> str:
    lines = ["[GAZE_TARGETS]", *(proposal["gaze_target_candidates"] or ["NONE"]), "", "[INITIAL]"]
    for actor in characters:
        state = proposal["initial_states"][actor]
        lines += ["", actor, f"affect: {state['affect']}", f"gaze: {state['gaze']}", f"reason: {proposal['initial_reasons'][actor]}"]
        if state.get("head") and state["head"] != "HEAD-NONE": lines.append(f"head: {state['head']}")
    lines += ["", "[CHANGES]"]
    for event in proposal["events"]:
        lines += ["", event["event_id"], f"actor: {event['actor']}", f"anchor: {event['anchor_id']}", f"reason: {event['reason']}"]
        for channel in ("affect", "gaze", "head", "blink"):
            if channel in event["changes"]: lines.append(f"{channel}: {event['changes'][channel]}")
    return "\n".join(lines) + "\n"
