"""Shared closed vocabularies for semantic performance compilation."""

from __future__ import annotations

PERSISTENT_CHANNELS = ("affect", "gaze", "head")
BLINK_VALUES = {"SLOW_BLINK", "DOUBLE_BLINK", "EYE_CLOSE_HOLD", "EYE_OPEN"}
HEAD_VALUES = {
    f"HEAD-{direction}-{strength}"
    for direction in ("UP", "DOWN", "TILT_LEFT", "TILT_RIGHT")
    for strength in ("SUBTLE", "MEDIUM", "STRONG")
} | {"HEAD-NONE"}
DIRECTION_TARGETS = {
    "RIGHT", "LEFT", "DOWN", "DOWN_LEFT", "DOWN_RIGHT", "UP", "UP_LEFT", "UP_RIGHT"
}
