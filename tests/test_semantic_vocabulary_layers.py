from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import yaml

from expregaze_jali.dual_performance_proposal_parser import parse_dual_performance_proposal
from expregaze_jali.performance_proposal_parser import (
    ProposalValidationError,
    load_semantic_vocabulary,
    parse_performance_proposal,
)


ROOT = Path(__file__).resolve().parents[1]
MAYA_TOOLS = ROOT / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from performance_score_model import (  # noqa: E402
    DEFAULT_HEART_STATES,
    DEFAULT_VISIBLE_AFFECTS,
    parse_score,
)


def _single(*, affect: str = "Watchful-40", heart: str = "Happy-40") -> str:
    return f"""[ANALYZE]
Agnes is curious about Will.

[PERFORMANCE]
S01
start: w0001
intent: GROWING_CURIOSITY_ABOUT_WILL
affect: {affect}
heart: {heart}
gaze: GAZE-B
head: MEDIUM
lid: -1
blink: NONE
blink_suppression: NONE

[REASONS]
S01.intent: Curiosity grows.
S01.affect: She observes him with contained interest.
S01.heart: The private feeling remains available.
S01.gaze: She studies him directly.
S01.head: She gives the assessment weight.
"""


def _dual(*, a_affect: str = "Watchful-40", b_affect: str = "Thinking-30", a_heart: str = "Happy-30", b_heart: str = "Angry-25") -> str:
    state = lambda alias, affect, heart, gaze: f"""{alias}.affect: {affect}
{alias}.heart: {heart}
{alias}.gaze: {gaze}
{alias}.head: NONE
{alias}.lid: NONE
{alias}.blink: NONE
{alias}.blink_suppression: NONE"""
    return f"""[ANALYZE]
Agnes is curious while Will thinks through his response.

[PERFORMANCE]
S01
start: w0001
intent: GROWING_CURIOSITY_ABOUT_WILL
{state("A", a_affect, a_heart, "GAZE-B")}
{state("B", b_affect, b_heart, "GAZE-A")}

[REASONS]
S01.intent: Curiosity is a shared beat.
S01.A.affect: Agnes stays observant.
S01.B.affect: Will thinks before speaking.
"""


def test_shared_json_v2_exactly_matches_the_jali_mask_config():
    shared = json.loads((ROOT / "configs" / "semantic_vocabulary.json").read_text(encoding="utf-8"))
    jali = yaml.safe_load((ROOT / "configs" / "jali_emotion_options.yaml").read_text(encoding="utf-8"))
    root = jali["jali_emotion"]
    visible = [name for name in root["mask"]["allowed_bearings"] if name != "Nothing"]
    heart = [name for name in root["heart"]["first_version_sources"] if name != "Nothing"]
    assert shared["schema_version"] == "semantic_vocabulary_v2"
    assert shared["visible_affect"] == visible + heart
    assert "heart" not in shared
    vocabulary = load_semantic_vocabulary()
    assert list(vocabulary.affect_states.values()) == visible + heart
    assert list(vocabulary.heart_states.values()) == heart
    assert DEFAULT_VISIBLE_AFFECTS == set(visible + heart)
    assert DEFAULT_HEART_STATES == set(heart)


@pytest.mark.parametrize("value", ["Watchful-40", "Thinking-40"])
def test_backend_visible_mask_accepts_only_closed_visible_vocabulary(value):
    parsed = parse_performance_proposal(_single(affect=value), vocabulary=load_semantic_vocabulary())
    assert parsed["phrases"][0]["affect"] == value


@pytest.mark.parametrize("value", ["Curious-40", "Warm-40"])
def test_backend_visible_mask_rejects_open_names(value):
    with pytest.raises(ProposalValidationError, match=f'Unknown affect state "{value.rsplit("-", 1)[0]}"'):
        parse_performance_proposal(_single(affect=value), vocabulary=load_semantic_vocabulary())


@pytest.mark.parametrize("value", ["Happy-40", "Angry-40", "Sad-40", "Surprised-40"])
def test_backend_heart_accepts_only_closed_heart_vocabulary(value):
    parsed = parse_performance_proposal(_single(heart=value), vocabulary=load_semantic_vocabulary())
    assert parsed["phrases"][0]["heart"] == value


@pytest.mark.parametrize("value", ["Angered-40", "Watchful-40", "Curious-40"])
def test_backend_heart_rejects_visible_and_open_names(value):
    with pytest.raises(ProposalValidationError, match=f'Unknown heart state "{value.rsplit("-", 1)[0]}"'):
        parse_performance_proposal(_single(heart=value), vocabulary=load_semantic_vocabulary())


def test_dual_parser_enforces_separate_a_b_executable_vocabularies():
    vocabulary = load_semantic_vocabulary()
    valid = parse_dual_performance_proposal(_dual(), vocabulary=vocabulary)
    assert valid["phrases"][0]["states"]["A"]["affect"] == "Watchful-40"
    assert valid["phrases"][0]["states"]["B"]["heart"] == "Angry-25"
    with pytest.raises(ProposalValidationError, match='Unknown A.affect state "Curious"'):
        parse_dual_performance_proposal(_dual(a_affect="Curious-40"), vocabulary=vocabulary)
    with pytest.raises(ProposalValidationError, match='Unknown A.heart state "Angered"'):
        parse_dual_performance_proposal(_dual(a_heart="Angered-30"), vocabulary=vocabulary)


def test_maya_single_and_dual_score_validation_uses_separate_closed_lists():
    assert parse_score("1. {ASSESS}\n<Watchful-40>\nLine.").valid
    assert parse_score("1. {ASSESS}\n<HEART-Happy-40>\nLine.").valid
    assert parse_score("1. {ASSESS}\n<HEART-Angry-40>\nLine.").valid
    assert parse_score("1. {ASSESS}\n<HEART-Sad-40>\nLine.").valid
    visible_error = parse_score("1. {ASSESS}\n<Curious-40>\nLine.").errors[0]
    assert str(visible_error) == 'Phrase 1: Unknown visible affect "Curious"'
    for value in ("Happy", "Angry"):
        assert parse_score(f"1. {{ASSESS}}\n<{value}-40>\nLine.").valid
    heart_error = parse_score("1. {ASSESS}\n<HEART-Angered-40>\nLine.").errors[0]
    assert str(heart_error) == 'Phrase 1: Unknown heart state "Angered"'
    valid_dual = parse_score(
        "1. {ASSESS}\nA:<Watchful-40> | B:<Thinking-30>\nA: Line.", mode="dual"
    )
    assert valid_dual.valid
    invalid_dual = parse_score(
        "1. {ASSESS}\nA:<Curious-40> | B:<Thinking-30>\nA: Line.", mode="dual"
    )
    assert str(invalid_dual.errors[0]) == 'Phrase 1: Unknown A visible affect "Curious"'


def test_prompts_explain_open_acting_language_and_closed_executable_vocabularies():
    single = (ROOT / "prompts" / "actor_performance_proposal_prompt_v3.md").read_text(encoding="utf-8")
    dual = (ROOT / "prompts" / "actor_dual_performance_proposal_prompt_v1.md").read_text(encoding="utf-8")
    assert "Actor-level interpretation is open vocabulary" in single
    assert "Curious, Warm, Interested" in single
    assert "Curious -> Watchful" not in single
    assert "fixed Curious-to-Watchful mapping" in single
    assert "positive integer percentage" in dual
    assert "only a visible affect listed in `[SEMANTIC VOCABULARY]`" in single
    assert "only a listed heart state" in single
    assert "Never output Heart" in dual and ".heart" not in dual
    assert "MASK-NONE" in dual and "Neutral is not NONE" in dual
