from __future__ import annotations

from pathlib import Path
import sys


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from performance_score_model import (  # noqa: E402
    DualPerformanceScoreModel,
    mask_intensity_display,
)


def _plan() -> dict:
    return {
        "schema_version": "dual_performance_plan_v1",
        "characters": ["ALICE", "BOB"],
        "phrases": [{
            "phrase_id": "P01", "speaker": "ALICE", "intent": "UNTOUCHED_INTENT",
            "span": {"text": "Good day.", "char_start": 0, "char_end": 9},
            "states": {
                "ALICE": {"affect": "Happy-80", "gaze": "GAZE-BOB", "head": "LOW", "lid": "NONE", "blink": "NONE", "blink_suppression": "NONE"},
                "BOB": {"affect": "Nervous-120", "gaze": "AVERT-ALICE", "head": "NONE", "lid": -1, "blink": "NONE", "blink_suppression": "NONE"},
            },
            "rationale": {"intent": "A retained intention.", "ALICE": {"affect": "Warm greeting."}, "BOB": {"gaze": "Avoids eye contact."}},
        }],
    }


def test_v1_score_is_name_keyed_mask_only_and_preserves_intent_on_edit():
    model = DualPerformanceScoreModel(_plan())
    score = model.score_text
    assert score.startswith("1.\n")
    assert "{UNTOUCHED_INTENT}" not in score and "HEART" not in score
    assert not any(line.lstrip().startswith(("A:<", "B:<")) for line in score.splitlines())
    assert "ALICE:<Happy-80><GAZE-BOB><HEAD-LOW>" in score
    assert "BOB:<Nervous-120><AVERT-ALICE><l-1>" in score
    assert score.rstrip().endswith("ALICE: Good day.")

    applied = model.apply(score.replace("<Happy-80>", "<Watchful-200>"))
    phrase = applied["phrases"][0]
    assert phrase["states"]["ALICE"]["affect"] == "Watchful-200"
    assert phrase["states"]["BOB"]["affect"] == "Nervous-120"
    assert phrase["speaker"] == "ALICE" and phrase["span"]["text"] == "Good day."
    assert phrase["intent"] == "UNTOUCHED_INTENT"
    assert "heart" not in phrase["states"]["ALICE"]
    assert "UNTOUCHED_INTENT" in model.rationale_view(1)
    assert "Heart" not in model.rationale_view(1)


def test_v1_score_rejects_aliases_unknown_names_and_invalid_intensity():
    model = DualPerformanceScoreModel(_plan())
    score = model.score_text
    assert any('Unknown character "A"' in str(issue) for issue in model.validate(score.replace("ALICE:<", "A:<", 1)).errors)
    assert any('Unknown character "FRED"' in str(issue) for issue in model.validate(score.replace("BOB:<", "FRED:<", 1)).errors)
    assert not model.validate(score.replace("Happy-80", "Thinking-31")).errors
    assert not model.validate(score.replace("Happy-80", "Watchful-200")).errors
    assert any('Unknown ALICE visible affect "Foo"' in str(issue) for issue in model.validate(score.replace("Happy-80", "Foo-80")).errors)
    assert model.validate(score.replace("Happy-80", "Happy-80.5")).errors
    inactive = score.replace("<Happy-80>", "")
    assert DualPerformanceScoreModel(_plan()).apply(inactive)["phrases"][0]["states"]["ALICE"]["affect"] == "NONE"


def test_mask_intensity_display_never_quantizes_custom_values():
    assert mask_intensity_display(5) == "Trace (5%)"
    assert mask_intensity_display(80) == "Measured (80%)"
    assert mask_intensity_display(100) == "Expressive (100%)"
    assert mask_intensity_display(120) == "Forceful (120%)"
    assert mask_intensity_display(200) == "Ludicrous (200%)"
    assert mask_intensity_display(73) == "Custom (73%)"


def test_ui_no_longer_builds_the_removed_hidden_affect_debug_inspector():
    source = (MAYA_TOOLS / "performance_plan_ui.py").read_text(encoding="utf-8")
    advanced = source.split("    def _build_advanced_tab", 1)[1].split(
        "    def _select_audio_folder", 1
    )[0]
    assert "self.hidden_affect = self._create_table" not in advanced
