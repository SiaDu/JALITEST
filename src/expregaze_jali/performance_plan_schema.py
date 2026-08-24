from __future__ import annotations

from typing import Any, NotRequired, TypedDict

SCHEMA_VERSION = "performance_plan_v0"


class TextSpan(TypedDict):
    text: str
    char_start: int
    char_end: int


class AffectStateSpan(TypedDict):
    source_tag: str
    char_start: int
    char_end: int
    value: str
    state: str | None
    intensity: float | None


class GazeStateSpan(TypedDict):
    source_tag: str
    char_start: int
    char_end: int
    value: str
    mode: str | None
    target: str | None


class HeadInvolvementSpan(TypedDict):
    source_tag: str
    char_start: int
    char_end: int
    value: str
    involvement: float | None


class LidStateSpan(TypedDict):
    source_tag: str
    char_start: int
    char_end: int
    value: str
    lid_state: int | float | str | None


class BlinkSpan(TypedDict):
    source_tag: str
    char_start: int
    char_end: int
    value: str


class RationaleEntry(TypedDict):
    source_tag: str
    reason: str | None


class BlinkRationale(TypedDict):
    performative: list[RationaleEntry]
    suppression: list[RationaleEntry]


class Rationale(TypedDict):
    intent: RationaleEntry | None
    affect: dict[str, list[RationaleEntry]]
    gaze: list[RationaleEntry]
    head: list[RationaleEntry]
    lid_state: list[RationaleEntry]
    blink: BlinkRationale


class Evidence(TypedDict):
    transcript: str


class Locks(TypedDict):
    intent: bool
    affect: bool
    gaze: bool
    head: bool
    blink: bool


class PerformancePlanEvent(TypedDict):
    event_id: str
    source_intent_tag: str
    span: TextSpan
    intent: str | None
    affect: dict[str, list[AffectStateSpan]]
    gaze: list[GazeStateSpan]
    head: list[HeadInvolvementSpan]
    lid_state: list[LidStateSpan]
    blink: dict[str, list[BlinkSpan]]
    rationale: Rationale
    evidence: Evidence
    locks: Locks


class Diagnostics(TypedDict):
    errors: list[str]
    warnings: list[str]


class PerformancePlan(TypedDict):
    schema_version: str
    sequence_id: str
    target_character: str | None
    source_annotation: str | None
    events: list[PerformancePlanEvent]
    diagnostics: Diagnostics
    acting_interpretation: NotRequired[str]


def default_locks() -> Locks:
    return {
        "intent": False,
        "affect": False,
        "gaze": False,
        "head": False,
        "blink": False,
    }


def assert_no_timing_fields(value: Any) -> None:
    """Reject timing-domain fields at the semantic Performance Plan boundary."""
    forbidden = {"seconds", "frames", "frame", "start_frame", "end_frame", "time", "timing"}

    def visit(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if str(key).lower() in forbidden:
                    raise ValueError(f"Performance Plan cannot contain timing field {path}.{key}")
                visit(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]")

    visit(value, "performance_plan")
