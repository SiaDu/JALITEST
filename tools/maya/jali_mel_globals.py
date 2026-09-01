"""Scoped access to JALI alignment globals shared by Maya workflows."""

from __future__ import annotations

from contextlib import contextmanager
import math
from typing import Any, Iterable


_JALI_ALIGNMENT_GLOBALS = (
    ("silence_handling", "int", "jalitest_get_silence_handling"),
    (
        "silence_handling_decibel",
        "float",
        "jalitest_get_silence_threshold",
    ),
    ("jali_afscratch", "int", "jalitest_get_animate_from_scratch"),
)


def _read_mel_global(mel_module: Any, name: str, mel_type: str, getter: str) -> Any:
    if not mel_module.eval(f'exists "{getter}"'):
        mel_module.eval(
            f"global proc {mel_type} {getter}() {{ "
            f"global {mel_type} ${name}; return ${name}; }}"
        )
    return mel_module.eval(f"{getter}()")


def _set_mel_global(
    mel_module: Any, name: str, mel_type: str, value: Any
) -> None:
    rendered = str(int(value)) if mel_type == "int" else repr(float(value))
    mel_module.eval(f"global {mel_type} ${name}; ${name} = {rendered};")


@contextmanager
def temporary_jali_alignment_globals(
    mel_module: Any,
    *,
    filter_silence_gaps: bool,
    silence_threshold_db: float,
    animate_from_scratch: bool,
) -> Iterable[None]:
    """Apply JALI alignment globals and restore them after success or failure."""
    if not isinstance(filter_silence_gaps, bool):
        raise ValueError("filter_silence_gaps must be true or false.")
    threshold = float(silence_threshold_db)
    if not math.isfinite(threshold) or not -100.0 <= threshold <= 0.0:
        raise ValueError("silence_threshold_db must be between -100 and 0 dB.")
    old = {
        name: _read_mel_global(mel_module, name, mel_type, getter)
        for name, mel_type, getter in _JALI_ALIGNMENT_GLOBALS
    }
    desired = {
        "silence_handling": int(filter_silence_gaps),
        "silence_handling_decibel": threshold,
        "jali_afscratch": int(bool(animate_from_scratch)),
    }
    try:
        for name, mel_type, _getter in _JALI_ALIGNMENT_GLOBALS:
            _set_mel_global(mel_module, name, mel_type, desired[name])
        yield
    finally:
        for name, mel_type, _getter in _JALI_ALIGNMENT_GLOBALS:
            _set_mel_global(mel_module, name, mel_type, old[name])
