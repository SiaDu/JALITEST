"""Direct deterministic construction of Performance Plan v0 from HCI proposals."""

from __future__ import annotations

import re
from typing import Any

from expregaze_jali.performance_plan_schema import (
    SCHEMA_VERSION,
    PerformancePlan,
    assert_no_timing_fields,
    default_locks,
)
from expregaze_jali.performance_proposal_parser import (
    HEAD_VALUES,
    ProposalValidationError,
    validate_proposal_anchors,
)
from expregaze_jali.transcript_anchor_model import TranscriptAnchorModel, speaker_key


_AFFECT = re.compile(r"^(.*)-(\d+)$")


class _TagCounters:
    def __init__(self) -> None:
        self.values = {prefix: 0 for prefix in ("i", "m", "h", "g", "hd", "l", "pb", "bs")}

    def next(self, prefix: str) -> str:
        self.values[prefix] += 1
        return f"{prefix}{self.values[prefix]:02d}"


def _reason(proposal: dict[str, Any], phrase_id: str, field: str, tag: str) -> dict[str, Any]:
    value = proposal.get("reasons", {}).get(phrase_id, {}).get(field)
    return {"source_tag": tag, "reason": value or None}


def _affect_span(tag: str, value: str, start: int, end: int) -> dict[str, Any]:
    match = _AFFECT.fullmatch(value)
    if match is None:  # semantic parser guarantees this
        raise ValueError(f"Invalid normalized affect value: {value}")
    state, intensity = match.groups()
    return {
        "source_tag": tag,
        "char_start": start,
        "char_end": end,
        "value": value,
        "state": state,
        "intensity": int(intensity) / 100.0,
    }


def _canonical_gaze(value: str, aliases: dict[str, str], phrase_id: str) -> tuple[str, str, str]:
    mode, target = value.split("-", 1)
    if target in {"A", "B"}:
        if target not in aliases:
            raise ProposalValidationError(
                f"{phrase_id}: gaze alias {target} is unavailable for this script"
            )
        target = f"CHARACTER_{speaker_key(aliases[target])}"
    return f"{mode}-{target}", mode, target


def build_performance_plan_from_proposal(
    proposal: dict[str, Any],
    *,
    anchor_model: TranscriptAnchorModel,
    sequence_id: str,
    proposal_path: str | None = None,
) -> PerformancePlan:
    """Build canonical JSON without XML, copied dialogue, or model-generated offsets."""
    phrases = validate_proposal_anchors(proposal, anchor_model)
    counters = _TagCounters()
    events: list[dict[str, Any]] = []

    for event_number, phrase in enumerate(phrases, 1):
        phrase_id = phrase["proposal_id"]
        start, end = int(phrase["char_start"]), int(phrase["char_end"])
        intent_tag = counters.next("i")
        rationale: dict[str, Any] = {
            "intent": _reason(proposal, phrase_id, "intent", intent_tag),
            "affect": {"visible": [], "hidden": []},
            "gaze": [],
            "head": [],
            "lid_state": [],
            "blink": {"performative": [], "suppression": []},
        }
        visible: list[dict[str, Any]] = []
        hidden: list[dict[str, Any]] = []
        gaze: list[dict[str, Any]] = []
        head: list[dict[str, Any]] = []
        lid_state: list[dict[str, Any]] = []
        performative: list[dict[str, Any]] = []
        suppression: list[dict[str, Any]] = []

        if phrase["affect"] != "NONE":
            tag = counters.next("m")
            visible.append(_affect_span(tag, phrase["affect"], start, end))
            rationale["affect"]["visible"].append(_reason(proposal, phrase_id, "affect", tag))
        if phrase["heart"] != "NONE":
            tag = counters.next("h")
            hidden.append(_affect_span(tag, phrase["heart"], start, end))
            rationale["affect"]["hidden"].append(_reason(proposal, phrase_id, "heart", tag))
        if phrase["gaze"] != "NONE":
            tag = counters.next("g")
            value, mode, target = _canonical_gaze(phrase["gaze"], anchor_model.aliases, phrase_id)
            gaze.append({
                "source_tag": tag, "char_start": start, "char_end": end,
                "value": value, "mode": mode, "target": target,
            })
            rationale["gaze"].append(_reason(proposal, phrase_id, "gaze", tag))

        # HEAD-NONE is an explicit, valid involvement decision rather than an inactive channel.
        tag = counters.next("hd")
        head.append({
            "source_tag": tag, "char_start": start, "char_end": end,
            "value": phrase["head"], "involvement": HEAD_VALUES[phrase["head"]],
        })
        rationale["head"].append(_reason(proposal, phrase_id, "head", tag))
        if phrase["lid"] is not None:
            tag = counters.next("l")
            value = str(phrase["lid"])
            lid_state.append({
                "source_tag": tag, "char_start": start, "char_end": end,
                "value": value, "lid_state": phrase["lid"],
            })
            rationale["lid_state"].append(_reason(proposal, phrase_id, "lid", tag))
        if phrase["blink"] != "NONE":
            tag = counters.next("pb")
            performative.append({
                "source_tag": tag, "char_start": start, "char_end": end, "value": phrase["blink"]
            })
            rationale["blink"]["performative"].append(_reason(proposal, phrase_id, "blink", tag))
        if phrase["blink_suppression"] == "SUPPRESS":
            tag = counters.next("bs")
            suppression.append({
                "source_tag": tag, "char_start": start, "char_end": end, "value": "SUPPRESS"
            })
            rationale["blink"]["suppression"].append(
                _reason(proposal, phrase_id, "blink_suppression", tag)
            )

        transcript = anchor_model.script[start:end]
        events.append({
            "event_id": f"E{event_number:02d}",
            "source_intent_tag": intent_tag,
            "span": {"text": transcript, "char_start": start, "char_end": end},
            "intent": phrase["intent"],
            "affect": {"visible": visible, "hidden": hidden},
            "gaze": gaze,
            "head": head,
            "lid_state": lid_state,
            "blink": {"performative": performative, "suppression": suppression},
            "rationale": rationale,
            "evidence": {"transcript": transcript},
            "locks": default_locks(),
        })

    provenance_phrases = [
        {
            **{key: value for key, value in phrase.items() if key not in {"text"}},
            "reasons": dict(proposal.get("reasons", {}).get(phrase["proposal_id"], {})),
        }
        for phrase in phrases
    ]
    diagnostics = proposal.get("diagnostics", {})
    plan: PerformancePlan = {
        "schema_version": SCHEMA_VERSION,
        "sequence_id": sequence_id,
        "target_character": anchor_model.target_character,
        "source_annotation": None,
        "source_proposal": proposal_path,
        "acting_interpretation": str(proposal.get("analyze") or ""),
        "events": events,
        "diagnostics": {
            "errors": list(diagnostics.get("errors", [])),
            "warnings": list(diagnostics.get("warnings", [])),
        },
        "proposal_provenance": {
            "format": "anchor_semantic_v1",
            "aliases": dict(anchor_model.aliases),
            "phrases": provenance_phrases,
        },
    }
    assert_no_timing_fields(plan)
    return plan
