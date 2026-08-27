from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from listener_mask_library import (  # noqa: E402
    AU_TO_USER_CONTROL,
    COMBINED_EXPORT_PATH,
    COMBINED_EXPORT_SHA256,
    FACTORY_EXPORT_PATH,
    FACTORY_EXPORT_SHA256,
    FACTORY_MASK_AUS,
    VISIBLE_AFFECT_STATES,
    exported_combined_rows,
    exported_factory_rows,
    is_eyelid_au,
    parse_mask_state,
    user_pose_for_mask,
)


def test_factory_export_integrity_and_semantic_vocabulary_match():
    factory_payload = json.loads(FACTORY_EXPORT_PATH.read_text(encoding="utf-8"))
    assert factory_payload["sha256_without_hash_field"] == FACTORY_EXPORT_SHA256
    assert factory_payload["rows"][0].startswith("name,index,au00_neutraA")
    vocabulary = json.loads((Path(__file__).resolve().parents[1] / "configs" / "semantic_vocabulary.json").read_text(encoding="utf-8"))
    assert set(vocabulary["visible_affect"]) == set(VISIBLE_AFFECT_STATES) == set(FACTORY_MASK_AUS)
    assert set(exported_factory_rows()) == set(VISIBLE_AFFECT_STATES)


def test_combined_export_preserves_factory_rows_and_excludes_custom_runtime_rows():
    combined_payload = json.loads(COMBINED_EXPORT_PATH.read_text(encoding="utf-8"))
    assert combined_payload["sha256_without_hash_field"] == COMBINED_EXPORT_SHA256
    combined = exported_combined_rows()
    factory = exported_factory_rows()
    assert {name: combined[name] for name in factory} == factory
    custom = {name for name in combined if name.endswith("_custom")}
    assert custom == {"Rage_custom", "Grief_custom", "Disdain_custom", "Disgust_custom", "Awe_custom", "Fear_custom", "Happy_custom", "IsolatedMask_custom", "IsolatedHeart_custom"}
    assert not custom & set(FACTORY_MASK_AUS)


def test_exported_factory_specific_rows_are_preserved_exactly():
    assert FACTORY_MASK_AUS["Panicky"] == {
        "au01_inBrowL": 7.5, "au01_inBrowR": 7.5, "au02_ouBrowL": 4.0,
        "au02_ouBrowR": 4.0, "au04_furroLL": 7.5, "au04_furroLR": 7.5,
        "au04_furroDL": 7.5, "au04_furroDR": 7.5,
    }
    assert FACTORY_MASK_AUS["Scheming"] == {
        "au01_inBrowL": 1.0, "au01_inBrowR": 1.0, "au04_furroLL": 7.5,
        "au04_furroLR": 7.5, "au04_furroDL": 3.0, "au04_furroDR": 3.0,
        "au12_smileL": 7.5, "au12_smileR": 4.0,
    }
    assert FACTORY_MASK_AUS["Devious"] == {
        "au04_furroLL": 7.5, "au04_furroLR": 7.5, "au04_furroDL": 7.5,
        "au04_furroDR": 7.5, "au10_sneerL": 1.0, "au10_sneerR": 1.0,
        "au12_smileL": 7.5, "au12_smileR": 7.5,
    }


def test_none_is_inactive_but_neutral_is_a_nonzero_factory_pose():
    assert parse_mask_state("NONE") == ("NONE", 0.0)
    assert all(value == 0.0 for value in user_pose_for_mask("NONE").values())
    neutral = user_pose_for_mask("Neutral-100")
    assert neutral["usr_InnerBrowRaise_L.InnerBrowRaise_L"] == 7.5
    assert neutral["usr_OuterBrowRaise_R.OuterBrowRaise_R"] == 7.5
    with pytest.raises(ValueError, match="Name-Intensity"):
        parse_mask_state("Neutral")


def test_all_factory_masks_and_native_jali_percentages_are_supported():
    assert {"Angry", "Sad", "Disgusted", "Afraid", "Contempt", "Surprised", "Happy"} <= set(FACTORY_MASK_AUS)
    assert parse_mask_state("Happy-100") == ("Happy", 100.0)
    assert parse_mask_state("Nervous-120") == ("Nervous", 120.0)
    assert parse_mask_state("Watchful-200") == ("Watchful", 200.0)
    assert parse_mask_state("Thinking-31") == ("Thinking", 31.0)
    assert user_pose_for_mask("Happy-50")["usr_Smile_L.Smile_L"] == FACTORY_MASK_AUS["Happy"]["au12_smileL"] * 0.5
    assert user_pose_for_mask("Watchful-200")["usr_OuterBrowRaise_L.OuterBrowRaise_L"] == FACTORY_MASK_AUS["Watchful"]["au02_ouBrowL"] * 2.0


def test_all_non_eyelid_factory_aus_are_explicitly_mapped_and_lids_are_filtered():
    used = {au for pose in FACTORY_MASK_AUS.values() for au in pose}
    assert all(au in AU_TO_USER_CONTROL or is_eyelid_au(au) for au in used)
    assert {au for au in used if is_eyelid_au(au)} >= {"au05_uLidUpL", "au07_lLidUpL", "au41_LidDwnR"}
    pose = user_pose_for_mask("Angered-100")
    assert pose["usr_Sneer_R.Sneer_R"] == 2.0
    assert pose["usr_LoLip_L_ctl.TightenFunnel_loLip_L"] == 2.0
    assert pose["usr_UpLip_R_ctl.DownUp_upLip_R"] == 1.0
    assert all("lid" not in plug.casefold() for plug in pose)
