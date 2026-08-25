from __future__ import annotations
import json, wave
from pathlib import Path
import pytest
from expregaze_jali.compile_dual_performance_plan import compile_dual_performance_plan
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

def test_shared_timing_preserves_listener_states_and_resumes_speaker_alignment(tmp_path):
    plan,audio=_fixture(tmp_path); result=compile_dual_performance_plan(performance_plan_path=plan,script=SCRIPT,script_source="script.txt",audio_folder=audio,fps=24,runtime_mapping=MAPPING,output_dir=tmp_path/"out")
    a=json.loads(Path(result["artifacts"]["A"]).read_text())["events"]; b=json.loads(Path(result["artifacts"]["B"]).read_text())["events"]
    assert [(x["phrase_id"],x["resolved_time"]["start"]) for x in a if x["channel"]=="affect"]==[("P01",0.),("P02",.5),("P03",1.)]
    assert [(x["phrase_id"],x["resolved_time"]["start"]) for x in b if x["channel"]=="affect"]==[("P01",0.),("P02",.5),("P03",1.)]
    assert result["full_script_source"]=="script.txt" and result["performance_plan_source"]==str(plan)

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
