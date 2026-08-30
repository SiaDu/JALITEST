from __future__ import annotations

import copy
import json

import pytest

from expregaze_jali.dual_performance_plan_v2 import build_dual_performance_plan_v2
from expregaze_jali.dual_sparse_performance_proposal_parser import parse_dual_sparse_performance_proposal
from expregaze_jali.performance_proposal_parser import ProposalValidationError, load_semantic_vocabulary
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model
from expregaze_jali.generate_dual_performance_plan import build_dual_generation_prompt


MODEL = build_conversation_anchor_model("ALICE: Hello there.\nBOB: No.", character_a="ALICE", character_b="BOB")


def parse(body: str):
    return parse_dual_sparse_performance_proposal(
        "[ANALYZE]\ntest\n[INITIAL]\nALICE\naffect: Watchful-80\ngaze: GAZE-BOB\nreason: Enters attentive.\n\nBOB\naffect: Nervous-60\ngaze: GAZE-ALICE\nreason: Enters guarded.\n[CHANGES]\n" + body,
        vocabulary=load_semantic_vocabulary(), anchor_model=MODEL,
    )


def test_gaze_target_candidates_keep_only_physical_calibration_metadata():
    base = "[INITIAL]\nALICE\naffect: Watchful-80\ngaze: GAZE-BOB\nreason: Enters attentive.\n\nBOB\naffect: Nervous-60\ngaze: GAZE-ALICE\nreason: Enters guarded.\n[CHANGES]\n"
    proposal = parse_dual_sparse_performance_proposal("[GAZE_TARGETS]\nletter\nWINDOW\n" + base, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    assert proposal["gaze_target_candidates"] == ["LETTER", "WINDOW"]
    assert parse_dual_sparse_performance_proposal("[GAZE_TARGETS]\nNONE\n" + base, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)["gaze_target_candidates"] == []
    cases = [
        ("UP_RIGHT", [], "ignored built-in non-calibration target: UP_RIGHT"),
        ("BOB", [], "ignored character target that requires no calibration: BOB"),
        ("UP_RIGHT\nNEW_HOUSE", ["NEW_HOUSE"], "UP_RIGHT"),
        ("ALICE\nWINDOW", ["WINDOW"], "ALICE"),
        ("UP\nDOWN\nBOB\nWINDOW", ["WINDOW"], "DOWN, UP"),
        (
            "UP\nDOWN\nLEFT\nRIGHT\nUP_LEFT\nWINDOW\nDOOR\nFIREPLACE\nTABLE\nFLOOR",
            ["WINDOW", "DOOR", "FIREPLACE", "TABLE", "FLOOR"],
            "DOWN, LEFT, RIGHT, UP, UP_LEFT",
        ),
    ]
    for entries, expected, warning in cases:
        parsed = parse_dual_sparse_performance_proposal(
            "[GAZE_TARGETS]\n" + entries + "\n" + base,
            vocabulary=load_semantic_vocabulary(), anchor_model=MODEL,
        )
        assert parsed["gaze_target_candidates"] == expected
        assert any(warning in item for item in parsed["diagnostics"]["warnings"])
    for invalid in ("LETTER\nLETTER", "A\nB\nC\nD\nE\nF", "FRONT DOOR", "LOOK AT WINDOW", "GAZE-WINDOW", "GLANCE-UP", "@WINDOW"):
        with pytest.raises(ProposalValidationError, match=r"candidate|unique"):
            parse_dual_sparse_performance_proposal("[GAZE_TARGETS]\n" + invalid + "\n" + base, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)


@pytest.mark.parametrize("entries", ["NONE\nLETTER", "WINDOW\nNONE", "NONE\nUP_RIGHT", ""])
def test_gaze_target_none_must_be_alone_and_new_section_cannot_be_empty(entries):
    base = "[INITIAL]\nALICE\naffect: Watchful-80\ngaze: GAZE-BOB\nreason: Enters attentive.\n\nBOB\naffect: Nervous-60\ngaze: GAZE-ALICE\nreason: Enters guarded.\n[CHANGES]\n"
    with pytest.raises(ProposalValidationError):
        parse_dual_sparse_performance_proposal("[GAZE_TARGETS]\n" + entries + "\n" + base, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)


def test_directional_metadata_is_tolerated_without_changing_executable_glance():
    proposal = parse_dual_sparse_performance_proposal(
        "[GAZE_TARGETS]\nUP_RIGHT\n"
        "[INITIAL]\nALICE\naffect: Watchful-80\ngaze: GAZE-BOB\nreason: Enters attentive.\n\n"
        "BOB\naffect: Nervous-60\ngaze: GAZE-ALICE\nreason: Enters guarded.\n"
        "[CHANGES]\nE001\nactor: ALICE\nanchor: w0001\ngaze: GLANCE-UP_RIGHT\nreason: Searches for the memory.\n\n"
        "E002\nactor: ALICE\nanchor: w0002\ngaze: GAZE-BOB\nreason: Returns attention to BOB.\n",
        vocabulary=load_semantic_vocabulary(), anchor_model=MODEL,
    )
    assert proposal["gaze_target_candidates"] == []
    assert proposal["events"][0]["changes"]["gaze"] == "GLANCE-UP_RIGHT"
    assert proposal["events"][1]["changes"]["gaze"] == "GAZE-BOB"
    assert proposal["diagnostics"]["warnings"] == [
        "[GAZE_TARGETS] ignored built-in non-calibration target: UP_RIGHT"
    ]
    plan = build_dual_performance_plan_v2(
        proposal, anchor_model=MODEL, sequence_id="directional-metadata"
    )
    assert plan["gaze_target_candidates"] == []
    assert plan["tracks"]["ALICE"][0]["changes"]["gaze"] == "GLANCE-UP_RIGHT"
    assert proposal["diagnostics"]["warnings"][0] in plan["diagnostics"]["warnings"]


def test_sparse_independent_tracks_and_resets():
    proposal = parse("""E001
actor: ALICE
anchor: w0001
gaze: GAZE-DOWN
reason: Looks directly.

E002
actor: BOB
anchor: w0002
affect: Happy-120
gaze: GLANCE-UP_LEFT
head: HEAD-TILT_LEFT-SUBTLE
blink: DOUBLE_BLINK
reason: Reacts.

E003
actor: BOB
anchor: w0003
affect: MASK-NONE
gaze: GAZE-DOWN
head: HEAD-NONE
reason: Releases the response.""")
    plan = build_dual_performance_plan_v2(proposal, anchor_model=MODEL, sequence_id="test")
    assert plan["schema_version"] == "dual_performance_plan_v2"
    assert plan["initial_states"]["ALICE"] == {"affect": "Watchful-80", "gaze": "GAZE-BOB", "head": "HEAD-NONE"}
    assert len(plan["tracks"]["ALICE"]) == 1 and len(plan["tracks"]["BOB"]) == 2
    assert plan["tracks"]["ALICE"][0]["changes"] == {"gaze": "GAZE-DOWN"}
    assert plan["tracks"]["BOB"][1]["changes"]["affect"] == "MASK-NONE"


@pytest.mark.parametrize("line", [
    "heart: Happy-80", "lid: 2", "blink_suppression: NONE",
    "head: HEAD-LOW", "gaze: RIGHT", "gaze: AVERT-RIGHT", "affect: Happy-0",
])
def test_v2_rejects_removed_or_invalid_semantics(line):
    with pytest.raises(ProposalValidationError):
        parse(f"E001\nactor: ALICE\nanchor: w0001\n{line}\nreason: invalid")


def test_v2_rejects_unknown_actor_anchor_and_empty_event():
    for body in (
        "E001\nactor: CAROL\nanchor: w0001\ngaze: GAZE-BOB",
        "E001\nactor: ALICE\nanchor: w9999\ngaze: GAZE-BOB",
        "E001\nactor: ALICE\nanchor: w0001\nreason: empty",
    ):
        with pytest.raises(ProposalValidationError):
            parse(body)


def test_v2_rejects_two_events_for_one_actor_at_one_anchor_but_allows_multi_channel_event():
    duplicate = """E001
actor: ALICE
anchor: w0001
gaze: GAZE-BOB
reason: Looks.

E002
actor: ALICE
anchor: w0001
head: HEAD-DOWN-SUBTLE
reason: Lowers her head."""
    with pytest.raises(ProposalValidationError, match="at most one v2 event"):
        parse(duplicate)
    proposal = parse("""E001
actor: ALICE
anchor: w0001
gaze: GAZE-BOB
head: HEAD-DOWN-SUBTLE
reason: Looks while lowering her head.

E002
actor: BOB
anchor: w0001
head: HEAD-UP-SUBTLE
reason: Independently reacts.""")
    assert proposal["events"][0]["changes"] == {"gaze": "GAZE-BOB", "head": "HEAD-DOWN-SUBTLE"}


def test_v2_prompt_treats_aversion_and_thinking_as_motivation_only():
    prompt = build_dual_generation_prompt(script="ALICE: Hello there.\nBOB: No.", character_a="ALICE", character_b="BOB")
    assert "avoiding eye contact" in prompt and "thinking" in prompt and "recalling" in prompt
    assert "GAZE-NONE, GLANCE-NONE, and AVERT are never executable authored gaze modes" in prompt
    assert "GAZE and GLANCE have different temporal semantics" in prompt
    assert "INTERNAL ATTENTION AND DIRECTIONAL GAZE" in prompt
    assert "[GAZE_TARGETS] is calibration metadata only" in prompt
    assert "built-in directions are executable gaze choices, not [GAZE_TARGETS] calibration candidates" in prompt
    assert "UP_RIGHT must NOT appear as a bare line under [GAZE_TARGETS]" in prompt
    assert "These are expressive acting priors, not fixed psychological codes" in prompt
    assert "GLANCE does not replace that persistent gaze" in prompt
    assert "Never repeat the same active `GAZE-*` value" in prompt
    assert "A prior `GLANCE-*` does not change the persistent gaze" in prompt
    assert "gaze: GLANCE-DOWN" in prompt and "gaze: GLANCE-UP_LEFT" in prompt
    assert "Do not map an emotion or motivation to a fixed direction" in prompt
    assert not __import__("re").search(r"gaze:\s*AVERT-", prompt)
    assert "Listeners may react during another actor's utterance" in prompt
    assert "earliest semantically sufficient heard cue word" in prompt
    assert "Do not automatically wait for sentence completion, dialogue-turn completion" in prompt
    assert "Both actors enter the scene already performing" in prompt and "Initial affect may not be `MASK-NONE`" in prompt
    assert "There is no fixed event count" in prompt


def _normalization_proposal(events, *, alice_initial=None, bob_initial=None):
    return {
        "initial_states": {
            "ALICE": {"affect": "Watchful-80", "gaze": "GAZE-BOB", "head": "HEAD-NONE", **(alice_initial or {})},
            "BOB": {"affect": "Neutral-60", "gaze": "GAZE-ALICE", "head": "HEAD-NONE", **(bob_initial or {})},
        },
        "initial_reasons": {"ALICE": "Begins watchful.", "BOB": "Begins composed."},
        "events": events,
        "diagnostics": {"errors": [], "warnings": []},
    }


def _event(event_id, actor, anchor_id, changes):
    return {"event_id": event_id, "actor": actor, "anchor_id": anchor_id, "changes": changes, "reason": f"{event_id} rationale."}


def test_canonical_normalizer_removes_repeated_persistent_gaze_but_keeps_real_change():
    proposal = _normalization_proposal([_event("E001", "ALICE", "w0001", {"affect": "Watchful-90", "gaze": "GAZE-BOB"})])
    source_copy = copy.deepcopy(proposal)
    plan = build_dual_performance_plan_v2(
        proposal,
        anchor_model=MODEL, sequence_id="normalization",
    )
    assert plan["tracks"]["ALICE"][0]["changes"] == {"affect": "Watchful-90"}
    assert plan["diagnostics"]["warnings"] == ["E001: removed no-op persistent channel(s): gaze"]
    assert proposal == source_copy


def test_canonical_normalizer_drops_fully_noop_event_and_keeps_blink():
    plan = build_dual_performance_plan_v2(
        _normalization_proposal([
            _event("E001", "ALICE", "w0001", {"gaze": "GAZE-BOB", "head": "HEAD-NONE"}),
            _event("E002", "ALICE", "w0002", {"head": "HEAD-NONE", "blink": "DOUBLE_BLINK"}),
        ]), anchor_model=MODEL, sequence_id="normalization",
    )
    assert [event["event_id"] for event in plan["tracks"]["ALICE"]] == ["E002"]
    assert plan["tracks"]["ALICE"][0]["changes"] == {"blink": "DOUBLE_BLINK"}
    assert "E001: removed no-op persistent channel(s): head, gaze" in plan["diagnostics"]["warnings"]
    assert "E001: dropped after no semantic changes remained" in plan["diagnostics"]["warnings"]
    assert "E002: removed no-op persistent channel(s): head" in plan["diagnostics"]["warnings"]


def test_glance_does_not_change_persistent_gaze_and_real_gaze_or_affect_change_remains():
    plan = build_dual_performance_plan_v2(
        _normalization_proposal([
            _event("E001", "ALICE", "w0001", {"gaze": "GLANCE-DOWN"}),
            _event("E002", "ALICE", "w0002", {"gaze": "GAZE-BOB"}),
            _event("E003", "ALICE", "w0003", {"gaze": "GAZE-DOWN", "affect": "Watchful-90"}),
        ]), anchor_model=MODEL, sequence_id="normalization",
    )
    assert [(event["event_id"], event["changes"]) for event in plan["tracks"]["ALICE"]] == [
        ("E001", {"gaze": "GLANCE-DOWN"}), ("E003", {"gaze": "GAZE-DOWN", "affect": "Watchful-90"}),
    ]
    assert "E002: dropped after no semantic changes remained" in plan["diagnostics"]["warnings"]


def test_persistent_normalization_is_actor_independent_and_uses_anchor_chronology():
    plan = build_dual_performance_plan_v2(
        _normalization_proposal([
            _event("E010", "ALICE", "w0002", {"gaze": "GAZE-DOWN"}),
            _event("E002", "BOB", "w0001", {"gaze": "GAZE-ALICE"}),
            _event("E999", "ALICE", "w0001", {"gaze": "GAZE-DOWN"}),
        ]), anchor_model=MODEL, sequence_id="normalization",
    )
    assert [(event["event_id"], event["changes"]) for event in plan["tracks"]["ALICE"]] == [("E999", {"gaze": "GAZE-DOWN"})]
    assert plan["tracks"]["BOB"] == []
    assert "E010: dropped after no semantic changes remained" in plan["diagnostics"]["warnings"]
    assert "E002: dropped after no semantic changes remained" in plan["diagnostics"]["warnings"]


def test_joan_persistent_gaze_regression_is_normalized_in_written_canonical_plan(tmp_path):
    anchors = build_conversation_anchor_model("JOAN: Earlier later.", character_a="JOAN", character_b="CHAYTON")
    proposal = {
        "initial_states": {
            "JOAN": {"affect": "Nervous-65", "gaze": "GAZE-CHAYTON", "head": "HEAD-NONE"},
            "CHAYTON": {"affect": "Neutral-60", "gaze": "GAZE-JOAN", "head": "HEAD-NONE"},
        },
        "initial_reasons": {"JOAN": "Begins tense.", "CHAYTON": "Begins attentive."},
        "events": [
            _event("E001", "JOAN", "w0001", {"affect": "Nervous-75", "gaze": "GAZE-FARMHOUSE_ENTRANCE"}),
            _event("E005", "JOAN", "w0002", {"affect": "Nervous-80", "gaze": "GAZE-FARMHOUSE_ENTRANCE"}),
        ],
        "diagnostics": {"errors": [], "warnings": []},
    }
    output = tmp_path / "performance_plan.json"
    output.write_text(json.dumps(build_dual_performance_plan_v2(proposal, anchor_model=anchors, sequence_id="joan")), encoding="utf-8")
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["tracks"]["JOAN"] == [
        {"event_id": "E001", "actor": "JOAN", "anchor_id": "w0001", "changes": {"affect": "Nervous-75", "gaze": "GAZE-FARMHOUSE_ENTRANCE"}, "reason": "E001 rationale."},
        {"event_id": "E005", "actor": "JOAN", "anchor_id": "w0002", "changes": {"affect": "Nervous-80"}, "reason": "E005 rationale."},
    ]


def test_initial_state_requires_visible_affect_and_reason_and_rejects_blink():
    source = "[ANALYZE]\nx\n[INITIAL]\nALICE\naffect: Happy-120\ngaze: GAZE-BOB\nreason: Enters openly.\n\nBOB\naffect: Neutral-60\ngaze: GAZE-ALICE\nreason: Enters composed.\n[CHANGES]\n"
    proposal = parse_dual_sparse_performance_proposal(source, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    assert proposal["initial_states"]["ALICE"] == {"affect": "Happy-120", "gaze": "GAZE-BOB", "head": "HEAD-NONE"}
    assert proposal["initial_states"]["BOB"] == {"affect": "Neutral-60", "gaze": "GAZE-ALICE", "head": "HEAD-NONE"}
    assert proposal["initial_reasons"]["ALICE"] == "Enters openly."


def test_authored_gaze_none_and_missing_initial_gaze_are_rejected():
    source = "[ANALYZE]\nx\n[INITIAL]\nALICE\naffect: Happy-80\ngaze: GAZE-NONE\nreason: x\nBOB\naffect: Neutral-60\ngaze: GAZE-ALICE\nreason: x\n[CHANGES]\n"
    with pytest.raises(ProposalValidationError, match="GAZE-NONE"):
        parse_dual_sparse_performance_proposal(source, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="GLANCE-NONE"):
        parse_dual_sparse_performance_proposal(source.replace("GAZE-NONE", "GLANCE-NONE"), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    source = source.replace("gaze: GAZE-NONE\n", "", 1)
    with pytest.raises(ProposalValidationError, match="gaze is required"):
        parse_dual_sparse_performance_proposal(source, vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    source = "[ANALYZE]\nx\n[INITIAL]\nALICE\naffect: Happy-120\ngaze: GAZE-BOB\nreason: Enters openly.\n\nBOB\naffect: Neutral-60\ngaze: GAZE-ALICE\nreason: Enters composed.\n[CHANGES]\n"
    with pytest.raises(ProposalValidationError, match="initial channel blink is not allowed"):
        parse_dual_sparse_performance_proposal(source.replace("affect: Happy-120", "blink: BLINK"), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="initial gaze must be persistent"):
        parse_dual_sparse_performance_proposal(source.replace("gaze: GAZE-ALICE", "gaze: GLANCE-DOWN"), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="requires one explicit actor block"):
        parse_dual_sparse_performance_proposal(source.replace("\nBOB\naffect: Neutral-60\ngaze: GAZE-ALICE\nreason: Enters composed.", ""), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="affect is required"):
        parse_dual_sparse_performance_proposal(source.replace("affect: Neutral-60\n", ""), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="visible Mask"):
        parse_dual_sparse_performance_proposal(source.replace("affect: Neutral-60", "affect: MASK-NONE"), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)
    with pytest.raises(ProposalValidationError, match="reason is required"):
        parse_dual_sparse_performance_proposal(source.replace("reason: Enters composed.", "reason:  "), vocabulary=load_semantic_vocabulary(), anchor_model=MODEL)


@pytest.mark.parametrize("blink", ["SLOW_BLINK", "DOUBLE_BLINK", "EYE_CLOSE_HOLD", "EYE_OPEN"])
def test_v2_authored_blink_vocabulary_accepts_only_explicit_performative_commands(blink):
    assert parse(f"E001\nactor: ALICE\nanchor: w0001\nblink: {blink}\nreason: A deliberate eye action.")["events"][0]["changes"]["blink"] == blink
    with pytest.raises(ProposalValidationError, match="Invalid performative blink"):
        parse("E001\nactor: ALICE\nanchor: w0001\nblink: BLINK\nreason: Invalid authored tag.")
