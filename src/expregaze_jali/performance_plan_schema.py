from __future__ import annotations

from typing import Any, TypedDict

SCHEMA_VERSION = "performance_plan_v0"


class TextSpan(TypedDict):
    text: str
    char_start: int
    char_end: int


class AffectLayer(TypedDict):
    state: str | None
    intensity: float | None


class Affect(TypedDict):
    visible: AffectLayer
    hidden: AffectLayer


class Gaze(TypedDict):
    mode: str | None
    target: str | None


class Head(TypedDict):
    involvement: float | None


class Blink(TypedDict):
    performative: str | None
    suppression: str | None


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
    affect: Affect
    gaze: Gaze
    head: Head
    lid_state: int | float | str | None
    blink: Blink
    rationale: str | None
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
