"""Versioned JALI 2025 factory Mask data for listener visible affect.

The data is read from checked-in JALI runtime exports, not from Maya/MEL globals.
Only the semantic visible-affect rows and their non-zero AU coefficients are
retained below, so custom runtime rows cannot affect listener realization.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Final


FACTORY_EXPORT_SHA256: Final = "c12abbd70add126bfd83b961a710be16f08874642b2fc56694a4112e520f997d"
COMBINED_EXPORT_SHA256: Final = "3f6d4550e95e3e6b4798662dbe457691e2f3f8c2c5aed34793cbc9bcf4505cad"
PROVENANCE: Final = (
    "Installed JALI Maya 2025 factory paralingual table; exported 2026-08-27; "
    f"SHA256 {FACTORY_EXPORT_SHA256}"
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FACTORY_EXPORT_PATH: Final = _REPOSITORY_ROOT / "resources" / "jali" / "factory_mask_table.json"
COMBINED_EXPORT_PATH: Final = _REPOSITORY_ROOT / "resources" / "jali" / "combined_mask_table.json"

# Expressive eyelid AUs are persistent Mask pose, distinct from the central
# transient blink control. These mappings are live-validated on Angela and
# ValleyGirl JALI 2025 rigs.
EYELID_AU_PREFIXES: Final = ("au05_", "au07_", "au41_")
EYELID_AUS: Final = frozenset(EYELID_AU_PREFIXES)

# This is deliberately independent from the factory's Heart-only rows.
VISIBLE_AFFECT_STATES: Final = frozenset({
    "Neutral", "Polite", "Friendly", "Sassy", "Smug", "Cocky", "Nervous",
    "Panicky", "Thinking", "Scheming", "Devious", "Devilish", "Provoked",
    "Angered", "Dislike", "Disgust", "Singing_Serene", "Watchful",
    "Intimidating", "Confused", "Lost", "Angry", "Sad", "Disgusted",
    "Afraid", "Contempt", "Surprised", "Happy",
})


def is_eyelid_au(au: str) -> bool:
    return au.casefold().startswith(EYELID_AU_PREFIXES)


def unmapped_expressive_eyelid_aus() -> tuple[str, ...]:
    """Report exported Mask eyelid AUs lacking a checked-in User-control mapping."""
    return tuple(sorted({
        au for pose in FACTORY_MASK_AUS.values() for au in pose
        if is_eyelid_au(au) and au not in AU_TO_USER_CONTROL
    }))


def _load_csv_rows(path: Path, *, header: list[str] | None = None) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = list(payload["rows"])
    if header is None:
        header, rows = rows[0], rows[1:]
        header = next(csv.reader([header]))
    return list(csv.DictReader(io.StringIO("\n".join(rows)), fieldnames=header))


def exported_factory_rows() -> dict[str, dict[str, float]]:
    """Parse the authoritative export into compact, non-zero coefficient rows."""
    result: dict[str, dict[str, float]] = {}
    for row in _load_csv_rows(FACTORY_EXPORT_PATH):
        name = row["name"]
        if name not in VISIBLE_AFFECT_STATES:
            continue
        result[name] = {
            au: value for au, raw in row.items()
            if au.startswith("au") and (value := float(raw)) != 0.0
        }
    if set(result) != VISIBLE_AFFECT_STATES:
        missing = sorted(VISIBLE_AFFECT_STATES - set(result))
        raise RuntimeError(f"Factory export is missing visible Mask states: {missing}")
    return result


def exported_combined_rows() -> dict[str, dict[str, float]]:
    """Parse the combined export using the factory export's CSV header."""
    factory_payload = json.loads(FACTORY_EXPORT_PATH.read_text(encoding="utf-8"))
    header = next(csv.reader([factory_payload["rows"][0]]))
    result: dict[str, dict[str, float]] = {}
    for row in _load_csv_rows(COMBINED_EXPORT_PATH, header=header):
        result[row["name"]] = {
            au: value for au, raw in row.items()
            if au.startswith("au") and (value := float(raw)) != 0.0
        }
    return result


# Only non-zero exported coefficients for the supported semantic vocabulary.
FACTORY_MASK_AUS: Final[dict[str, dict[str, float]]] = exported_factory_rows()

# Explicit namespace-free User FACSMaster controls. These correspond to the
# installed JALI 2025 conversion table and ValleyGirl usr_FACSMaster topology.
_AU_TO_USER_PLUG: Final[dict[str, str]] = {
    "au01_inBrowL": "usr_InnerBrowRaise_L.InnerBrowRaise_L", "au01_inBrowR": "usr_InnerBrowRaise_R.InnerBrowRaise_R",
    "au02_ouBrowL": "usr_OuterBrowRaise_L.OuterBrowRaise_L", "au02_ouBrowR": "usr_OuterBrowRaise_R.OuterBrowRaise_R",
    "au03_doBrowL": "usr_Squint_L.OuterSquint_L", "au03_doBrowR": "usr_Squint_R.OuterSquint_R",
    "au04_furroLL": "usr_BrowInDown_L.BrowIn_L", "au04_furroLR": "usr_BrowInDown_R.BrowIn_R",
    "au04_furroDL": "usr_BrowInDown_L.BrowDown_L", "au04_furroDR": "usr_BrowInDown_R.BrowDown_R",
    "au06_cheekL": "usr_CheekRaise_L.CheekRaise_L", "au06_cheekR": "usr_CheekRaise_R.CheekRaise_R",
    "au09_winceL": "usr_Wince_L.Wince_L", "au09_winceR": "usr_Wince_R.Wince_R",
    "au10_sneerL": "usr_Sneer_L.Sneer_L", "au10_sneerR": "usr_Sneer_R.Sneer_R",
    "au11_zigMinL": "usr_Smile_L.SadSmile_L", "au11_zigMinR": "usr_Smile_R.SadSmile_R",
    "au12_smileL": "usr_Smile_L.Smile_L", "au12_smileR": "usr_Smile_R.Smile_R",
    "au14_buccinL": "usr_Dimple_L.Dimple_L", "au14_buccinR": "usr_Dimple_R.Dimple_R",
    "au15_frownL": "usr_Frown_L.Frown_L", "au15_frownR": "usr_Frown_R.Frown_R",
    "au17_isoMentalL": "usr_ChinRaise_L.ChinRaise_loLip_L", "au17_isoMentalR": "usr_ChinRaise_R.ChinRaise_loLip_R",
    "au18_incisiL": "usr_Pucker_L.Pucker_L", "au18_incisiR": "usr_Pucker_R.Pucker_R",
    "au20_grimacL": "usr_Grimace_L.Grimace_L", "au20_grimacR": "usr_Grimace_R.Grimace_R",
    "au16_labInfL": "usr_LoLip_L_ctl.DownUp_loLip_L", "au16_labInfR": "usr_LoLip_R_ctl.DownUp_loLip_R",
    "au23_uLTighL": "usr_UpLip_L_ctl.TightenFunnel_upLip_L", "au23_uLTighR": "usr_UpLip_R_ctl.TightenFunnel_upLip_R",
    "au23_lLTighL": "usr_LoLip_L_ctl.TightenFunnel_loLip_L", "au23_lLTighR": "usr_LoLip_R_ctl.TightenFunnel_loLip_R",
    "au24_lLPresL": "usr_LoLip_L_ctl.DownUp_loLip_L", "au24_lLPresR": "usr_LoLip_R_ctl.DownUp_loLip_R",
    "au25_lipParL": "usr_UpLip_L_ctl.DownUp_upLip_L", "au25_lipParR": "usr_UpLip_R_ctl.DownUp_upLip_R",
    "au44_SquintL": "usr_Squint_L.Squint_L", "au44_SquintR": "usr_Squint_R.Squint_R",
}

# One authoritative AU conversion table. A multiplier is necessary because
# AU05 and AU41 intentionally share side-specific LidDown plugs with opposite
# polarities; callers must accumulate rather than overwrite shared plugs.
AU_TO_USER_CONTROL: Final[dict[str, dict[str, float | str]]] = {
    au: {"plug": plug, "multiplier": 1.0} for au, plug in _AU_TO_USER_PLUG.items()
} | {
    "au05_uLidUpL": {"plug": "usr_blink_L.LidDown_L", "multiplier": -1.0},
    "au05_uLidUpR": {"plug": "usr_blink_R.LidDown_R", "multiplier": -1.0},
    "au07_lLidUpL": {"plug": "usr_loLid_L.LidUp_L", "multiplier": 1.0},
    "au07_lLidUpR": {"plug": "usr_loLid_R.LidUp_R", "multiplier": 1.0},
    "au41_LidDwnL": {"plug": "usr_blink_L.LidDown_L", "multiplier": 1.0},
    "au41_LidDwnR": {"plug": "usr_blink_R.LidDown_R", "multiplier": 1.0},
}


def mapped_user_plugs() -> tuple[str, ...]:
    """Namespace-free User controls managed by listener Mask realization."""
    return tuple(sorted({str(mapping["plug"]) for mapping in AU_TO_USER_CONTROL.values()}))


def _validate_au_classification() -> None:
    unknown = sorted({
        au for pose in FACTORY_MASK_AUS.values() for au in pose
        if au not in AU_TO_USER_CONTROL
    })
    if unknown:
        raise RuntimeError(f"Unclassified non-eyelid listener Mask AUs: {unknown}")


_validate_au_classification()


def parse_mask_state(value: object) -> tuple[str, float]:
    """Return a validated ``(name, intensity)`` pair; NONE is inactive."""
    raw = str(value or "").strip()
    if not raw or raw.upper() == "NONE":
        return "NONE", 0.0
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
    if amount <= 0 or not amount.is_integer():
        raise ValueError(f"Mask intensity must be a positive integer percentage, got {raw!r}.")
    return canonical, amount


def user_pose_for_mask(value: object) -> dict[str, float]:
    """Resolve one Mask into accumulated persistent User-lane values."""
    name, intensity = parse_mask_state(value)
    result = {plug: 0.0 for plug in mapped_user_plugs()}
    if name == "NONE":
        return result
    scale = intensity / 100.0
    for au, coefficient in FACTORY_MASK_AUS[name].items():
        try:
            mapping = AU_TO_USER_CONTROL[au]
        except KeyError as exc:  # A future source change must never be silent.
            raise RuntimeError(f"Unmapped listener Mask AU {au!r}.") from exc
        plug = str(mapping["plug"])
        result[plug] += coefficient * scale * float(mapping["multiplier"])
    return result
