from __future__ import annotations
import json, wave
from pathlib import Path
import pytest
from expregaze_jali.compile_dual_performance_plan import build_canonical_phrase_timeline, compile_dual_performance_plan
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model

SCRIPT="AGNES: one\nWILL: two\nAGNES: three"
MAPPING={"A":{"script_name":"AGNES","sound_file":"SeqT_AGNES"},"B":{"script_name":"WILL","sound_file":"SeqT_WILL"}}

def _wav(path:Path):
    with wave.open(str(path),"wb") as f: f.setnchannels(1); f.setsampwidth(2); f.setframerate(100); f.writeframes(b"\0\0"*200)
def _words(path:Path, rows): path.write_text("\n".join(json.dumps(x) for x in rows)+"\n")
def _fixture(tmp_path:Path):
    audio=tmp_path/"audio"; audio.mkdir(); _wav(audio/"SeqT_AGNES.wav"); _wav(audio/"SeqT_WILL.wav")
    _words(audio/"SeqT_AGNES_words.jsonl",[{"word":"one","start":0.,"end":.2},{"word":"three","start":1.,"end":1.2}]); _words(audio/"SeqT_WILL_words.jsonl",[{"word":"two","start":.5,"end":.7}])
    model=build_conversation_anchor_model(SCRIPT,character_a="AGNES",character_b="WILL")
    phrases=[]
    for i,turn in enumerate(model.turns,1):
        phrases.append({"phrase_id":f"P{i:02}","source_proposal_id":f"S{i:02}","speaker":"A" if turn.speaker=="AGNES" else "B","span":{"turn_id":turn.turn_id,"char_start":turn.anchors[0].char_start,"char_end":turn.anchors[-1].char_end,"text":turn.utterance_text},"intent":"BEAT","states":{"A":{"affect":"Friendly-50","heart":"NONE","gaze":"NONE","head":"NONE","lid":None,"blink":"NONE","blink_suppression":"NONE"},"B":{"affect":"Thinking-40","heart":"NONE","gaze":"NONE","head":"NONE","lid":None,"blink":"NONE","blink_suppression":"NONE"}},"rationale":{"A":{},"B":{}}})
    plan=tmp_path/"plan.json"; plan.write_text(json.dumps({"schema_version":"dual_performance_plan_v0","characters":{"A":"AGNES","B":"WILL"},"phrases":phrases}))
    return plan,audio


def _timeline_fixture(script, phrase_ids, raw_times):
    model = build_conversation_anchor_model(script, character_a="AGNES", character_b="WILL")
    phrases = []
    for phrase_id, turn in zip(phrase_ids, model.turns):
        phrases.append({"phrase_id": phrase_id, "speaker": "A" if turn.speaker == "AGNES" else "B", "span": {"turn_id": turn.turn_id, "char_start": turn.anchors[0].char_start, "char_end": turn.anchors[-1].char_end}})
    anchor_times = {}
    for turn, (start, end) in zip(model.turns, raw_times):
        anchor_times[turn.anchors[0].anchor_id] = {"start": start, "end": end}
    return phrases, model, anchor_times


def test_canonical_phrase_timeline_preserves_plan_order_and_repairs_cross_speaker_overlap():
    phrases, model, times = _timeline_fixture("AGNES: alpha\nWILL: beta", ["P03", "P04"], [(5.648625, 6.868625), (4.150437, 9.194)])
    timeline = build_canonical_phrase_timeline(phrases, model, times)
    assert [item["phrase_id"] for item in timeline] == ["P03", "P04"]
    assert timeline[0]["canonical_start"] == 5.648625
    assert timeline[0]["canonical_end"] == 6.868625
    assert timeline[1]["canonical_start"] == 6.868625
    assert timeline[1]["canonical_end"] == 9.194
    assert timeline[1]["start_adjusted"] is True


def test_canonical_phrase_timeline_preserves_real_pauses_and_second_overlap_order():
    phrases, model, times = _timeline_fixture("AGNES: alpha\nWILL: beta", ["P08", "P09"], [(27.416880, 29.336878), (25.481064, 32.972626)])
    timeline = build_canonical_phrase_timeline(phrases, model, times)
    assert [item["canonical_start"] for item in timeline] == [27.416880, 29.336878]
    phrases, model, times = _timeline_fixture("AGNES: alpha\nWILL: beta", ["P1", "P2"], [(0.0, 2.0), (3.5, 4.0)])
    timeline = build_canonical_phrase_timeline(phrases, model, times)
    assert [(item["canonical_start"], item["canonical_end"]) for item in timeline] == [(0.0, 3.5), (3.5, 4.0)]
    assert all(item["canonical_end"] > item["canonical_start"] + 1e-6 for item in timeline)


def test_canonical_phrase_timeline_rejects_collapsed_phrase_after_alignment_repair():
    phrases, model, times = _timeline_fixture(
        "AGNES: one\nWILL: two\nAGNES: three",
        ["P1", "P2", "P3"],
        [(0.0, 10.0), (1.0, 2.0), (3.0, 4.0)],
    )
    with pytest.raises(ValueError, match="Canonical timing collapsed phrase P2 to zero duration"):
        build_canonical_phrase_timeline(phrases, model, times)


def test_canonical_phrase_timeline_keeps_same_turn_splits_on_their_own_anchors():
    model = build_conversation_anchor_model("AGNES: one two three", character_a="AGNES", character_b="WILL")
    turn = model.turns[0]
    phrases = [
        {"phrase_id": "P09", "speaker": "A", "span": {"turn_id": turn.turn_id, "char_start": turn.anchors[0].char_start, "char_end": turn.anchors[0].char_end}},
        {"phrase_id": "P10", "speaker": "A", "span": {"turn_id": turn.turn_id, "char_start": turn.anchors[1].char_start, "char_end": turn.anchors[-1].char_end}},
    ]
    times = {
        turn.anchors[0].anchor_id: {"start": 1.0, "end": 1.2},
        turn.anchors[1].anchor_id: {"start": 2.0, "end": 2.2},
        turn.anchors[2].anchor_id: {"start": 3.0, "end": 3.2},
    }
    timeline = build_canonical_phrase_timeline(phrases, model, times)
    assert [(item["canonical_start"], item["canonical_end"]) for item in timeline] == [(1.0, 2.0), (2.0, 3.2)]

def test_shared_timing_preserves_listener_states_and_resumes_speaker_alignment(tmp_path):
    plan,audio=_fixture(tmp_path); result=compile_dual_performance_plan(performance_plan_path=plan,script=SCRIPT,script_source="script.txt",audio_folder=audio,fps=24,runtime_mapping=MAPPING,output_dir=tmp_path/"out")
    a=json.loads(Path(result["artifacts"]["A"]).read_text())["events"]; b=json.loads(Path(result["artifacts"]["B"]).read_text())["events"]
    assert [(x["phrase_id"],x["resolved_time"]["start"]) for x in a if x["channel"]=="affect"]==[("P01",0.),("P02",.5),("P03",1.)]
    assert [(x["phrase_id"],x["resolved_time"]["start"]) for x in b if x["channel"]=="affect"]==[("P01",0.),("P02",.5),("P03",1.)]
    assert [x["resolved_time"] for x in a] == [x["resolved_time"] for x in b]
    phrase_timing = json.loads(Path(result["artifacts"]["conversation_phrase_timing"]).read_text())["phrases"]
    assert [row["phrase_id"] for row in phrase_timing] == ["P01", "P02", "P03"]
    assert all(row["canonical_end"] == phrase_timing[index + 1]["canonical_start"] for index, row in enumerate(phrase_timing[:-1]))
    assert result["full_script_source"]=="script.txt" and result["performance_plan_source"]==str(plan)


def test_all_channels_and_actors_receive_one_canonical_phrase_interval(tmp_path):
    plan, audio = _fixture(tmp_path)
    payload = json.loads(plan.read_text())
    for phrase in payload["phrases"]:
        for alias in ("A", "B"):
            phrase["states"][alias].update({"affect": "Friendly-50", "heart": "Happy-10", "gaze": f"GAZE-{'B' if alias == 'A' else 'A'}", "head": "LOW", "lid": -1, "blink": "DOUBLE_BLINK", "blink_suppression": "SUPPRESS"})
    plan.write_text(json.dumps(payload))
    result = compile_dual_performance_plan(performance_plan_path=plan, script=SCRIPT, audio_folder=audio, fps=24, runtime_mapping=MAPPING, output_dir=tmp_path / "out")
    actor_events = {alias: json.loads(Path(result["artifacts"][alias]).read_text())["events"] for alias in ("A", "B")}
    for phrase_id in ("P01", "P02", "P03"):
        times = [[event["resolved_time"] for event in actor_events[alias] if event["phrase_id"] == phrase_id] for alias in ("A", "B")]
        assert all(time == times[0][0] for rows in times for time in rows)

@pytest.mark.parametrize("kind",["missing","extra","mismatch"])
def test_alignment_failures_are_explicit(tmp_path,kind):
    plan,audio=_fixture(tmp_path)
    if kind=="missing": (audio/"SeqT_WILL_words.jsonl").unlink()
    elif kind=="extra": _words(audio/"SeqT_WILL_words.jsonl",[{"word":"two","start":.5,"end":.7},{"word":"extra","start":1.,"end":1.1}])
    else: _words(audio/"SeqT_WILL_words.jsonl",[{"word":"wrong","start":.5,"end":.7}])
    with pytest.raises((FileNotFoundError,ValueError)): compile_dual_performance_plan(performance_plan_path=plan,script=SCRIPT,audio_folder=audio,fps=24,runtime_mapping=MAPPING,output_dir=tmp_path/"out")

def test_mapping_and_ambiguous_alignment_fail(tmp_path):
    plan,audio=_fixture(tmp_path); (audio/"SeqT_AGNES.TextGrid").write_text("x")
    with pytest.raises(ValueError,match="does not match"): compile_dual_performance_plan(performance_plan_path=plan,script=SCRIPT,audio_folder=audio,fps=24,runtime_mapping={**MAPPING,"A":{"script_name":"WILL","sound_file":"SeqT_AGNES"}},output_dir=tmp_path/"out")
    with pytest.raises(ValueError,match="Ambiguous"): compile_dual_performance_plan(performance_plan_path=plan,script=SCRIPT,audio_folder=audio,fps=24,runtime_mapping=MAPPING,output_dir=tmp_path/"out")


def test_mismatched_wav_durations_use_shortest_shared_duration(tmp_path):
    plan, audio = _fixture(tmp_path)
    with wave.open(str(audio / "SeqT_WILL.wav"), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(100); f.writeframes(b"\0\0" * 150)
    result = compile_dual_performance_plan(performance_plan_path=plan, script=SCRIPT, audio_folder=audio, fps=24, runtime_mapping=MAPPING, output_dir=tmp_path / "out")
    assert result["shared_duration_seconds"] == 1.5
    assert "using the shortest shared duration" in result["warnings"][0]
