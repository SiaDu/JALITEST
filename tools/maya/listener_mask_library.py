"""Versioned, deterministic JALI 2025 factory Mask data for listener poses.

Source: JALI Maya 2025 factory Mask library embedded in JaliWin2025.mll,
reconnoitred 2026-08-27.  This deliberately avoids reading vendor binaries
during an animation run.
"""
from __future__ import annotations

from typing import Final


PROVENANCE: Final = "JALI Maya 2025 factory Mask library"
EYELID_AUS: Final = frozenset({"au05_uLidUpL", "au05_uLidUpR"})

# Only the initial JALITEST listener vocabulary.  Omitted AU columns are zero.
FACTORY_MASK_AUS: Final[dict[str, dict[str, float]]] = {
    "Polite": {"au01_inBrowL": 5, "au01_inBrowR": 5, "au02_ouBrowL": 5, "au02_ouBrowR": 5, "au12_smileL": 7.5, "au12_smileR": 6},
    "Friendly": {"au01_inBrowL": 6, "au01_inBrowR": 6, "au02_ouBrowL": 6, "au02_ouBrowR": 6, "au06_cheekL": 3, "au06_cheekR": 2.5, "au12_smileL": 7.5, "au12_smileR": 6},
    "Sassy": {"au01_inBrowL": 6, "au01_inBrowR": 7, "au02_ouBrowL": 4, "au02_ouBrowR": 5, "au04_furroLL": 7.5, "au04_furroLR": 7.5, "au04_furroDL": 7.5, "au04_furroDR": 7.5, "au12_smileL": 7.5, "au12_smileR": 5},
    "Smug": {"au01_inBrowL": 7.5, "au01_inBrowR": 5, "au02_ouBrowL": 2, "au02_ouBrowR": 1, "au04_furroLL": 7.5, "au04_furroLR": 7.5, "au04_furroDL": 7.5, "au04_furroDR": 7.5, "au06_cheekL": 4, "au06_cheekR": 3, "au12_smileL": 7.5, "au12_smileR": 2.5, "au44_SquintL": 4, "au44_SquintR": 3},
    "Nervous": {"au01_inBrowL": 7.5, "au01_inBrowR": 7.5, "au04_furroLL": 6, "au04_furroLR": 6, "au04_furroDL": 4, "au04_furroDR": 4},
    "Thinking": {"au01_inBrowL": 1, "au01_inBrowR": 1, "au04_furroLL": 7.5, "au04_furroLR": 7.5, "au04_furroDL": 3, "au04_furroDR": 3},
    "Watchful": {"au02_ouBrowL": 5, "au04_furroLL": 7.5, "au04_furroLR": 7.5, "au04_furroDL": 7.5, "au04_furroDR": 7.5},
    "Confused": {"au01_inBrowL": 5, "au01_inBrowR": 5, "au02_ouBrowL": 6, "au02_ouBrowR": 6, "au04_furroLL": 7.5, "au04_furroLR": 7.5, "au04_furroDL": 7.5, "au04_furroDR": 7.5, "au05_uLidUpL": 3, "au05_uLidUpR": 3, "au09_winceL": 2, "au09_winceR": 2, "au18_incisiL": 2, "au18_incisiR": 2, "au44_SquintL": 2, "au44_SquintR": 2},
    "Lost": {"au01_inBrowL": 7, "au01_inBrowR": 7, "au02_ouBrowL": 4, "au02_ouBrowR": 4, "au04_furroLL": 7.5, "au04_furroLR": 7.5, "au04_furroDL": 3, "au04_furroDR": 3, "au05_uLidUpL": 3, "au05_uLidUpR": 3},
}

# Explicit, namespace-free plugs.  The Maya caller adds the rig namespace.
AU_TO_USER_CONTROL: Final[dict[str, str]] = {
    "au01_inBrowL": "usr_InnerBrowRaise_L.InnerBrowRaise_L", "au01_inBrowR": "usr_InnerBrowRaise_R.InnerBrowRaise_R",
    "au02_ouBrowL": "usr_OuterBrowRaise_L.OuterBrowRaise_L", "au02_ouBrowR": "usr_OuterBrowRaise_R.OuterBrowRaise_R",
    "au04_furroLL": "usr_BrowInDown_L.BrowIn_L", "au04_furroLR": "usr_BrowInDown_R.BrowIn_R",
    "au04_furroDL": "usr_BrowInDown_L.BrowDown_L", "au04_furroDR": "usr_BrowInDown_R.BrowDown_R",
    "au06_cheekL": "usr_CheekRaise_L.CheekRaise_L", "au06_cheekR": "usr_CheekRaise_R.CheekRaise_R",
    "au09_winceL": "usr_Wince_L.Wince_L", "au09_winceR": "usr_Wince_R.Wince_R",
    "au12_smileL": "usr_Smile_L.Smile_L", "au12_smileR": "usr_Smile_R.Smile_R",
    "au18_incisiL": "usr_Pucker_L.Pucker_L", "au18_incisiR": "usr_Pucker_R.Pucker_R",
    "au44_SquintL": "usr_Squint_L.Squint_L", "au44_SquintR": "usr_Squint_R.Squint_R",
}


def parse_mask_state(value: object) -> tuple[str, float]:
    """Return a validated ``(name, intensity)`` pair, or neutral for NONE."""
    raw = str(value or "").strip()
    if not raw or raw.upper() in {"NONE", "NEUTRAL"}:
        return "NEUTRAL", 0.0
    name, separator, intensity = raw.rpartition("-")
    if not separator or not name or not intensity:
        raise ValueError(f"Mask state must be Name-Intensity, got {raw!r}.")
    canonical = next((item for item in FACTORY_MASK_AUS if item.casefold() == name.casefold()), None)
    if canonical is None:
        raise ValueError(f"Unsupported listener Mask {name!r}.")
    try:
        amount = float(intensity)
    except ValueError as exc:
        raise ValueError(f"Mask intensity must be numeric, got {raw!r}.") from exc
    if not 0.0 <= amount <= 100.0:
        raise ValueError(f"Mask intensity must be in [0, 100], got {raw!r}.")
    return canonical, amount


def user_pose_for_mask(value: object) -> dict[str, float]:
    """Resolve one Mask into eyelid-filtered User-lane plug values."""
    name, intensity = parse_mask_state(value)
    if name == "NEUTRAL":
        return {plug: 0.0 for plug in AU_TO_USER_CONTROL.values()}
    scale = intensity / 100.0
    result = {plug: 0.0 for plug in AU_TO_USER_CONTROL.values()}
    for au, coefficient in FACTORY_MASK_AUS[name].items():
        if au in EYELID_AUS:
            continue
        plug = AU_TO_USER_CONTROL.get(au)
        if plug:
            result[plug] = float(coefficient) * scale
    return result
