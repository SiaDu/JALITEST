from __future__ import annotations
import json, re, wave
from pathlib import Path
import pytest
from expregaze_jali.compile_dual_performance_plan import build_canonical_phrase_timeline, compile_dual_performance_plan, _validate_v2_plan, _compile_v2
from expregaze_jali.transcript_anchor_model import build_conversation_anchor_model

SCRIPT="AGNES: one\nWILL: two\nAGNES: three"
MAPPING={"A":{"script_name":"AGNES","sound_file":"SeqT_AGNES"},"B":{"script_name":"WILL","sound_file":"SeqT_WILL"}}

def _wav(path:Path):
    with wave.open(str(path),"wb") as f: f.setnchannels(1); f.setsampwidth(2); f.setframerate(100); f.writeframes(b"\0\0"*200)
def _words(path:Path, rows): path.write_text("\n".join(json.dumps(x) for x in rows)+"\n")
def _fixture(tmp_path:Path):
    audio=tmp_path/"audio"; audio.mkdir(); _wav(audio/"SeqT_AGNES.wav"); _wav(audio/"SeqT_WILL.wav")
    _words(audio/"SeqT_AGNES_words.jsonl",[{"word":"one","start":0.,"end":.2},{"word":"three","start":1.,"end":1.2}]); _words(audio/"SeqT_WILL_words.jsonl",[{"word":"two","start":.5,"end":.7}])
    (audio/"SeqT_AGNES.txt").write_text("one three",encoding="utf-8"); (audio/"SeqT_WILL.txt").write_text("two",encoding="utf-8")
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
    a=json.loads(Path(result["artifacts"]["characters"]["AGNES"]["semantic_events"]).read_text())["events"]; b=json.loads(Path(result["artifacts"]["characters"]["WILL"]["semantic_events"]).read_text())["events"]
    assert [(x["phrase_id"],x["resolved_time"]["start"]) for x in a if x["channel"]=="affect"]==[("P01",0.),("P02",.5),("P03",1.)]
    assert [(x["phrase_id"],x["resolved_time"]["start"]) for x in b if x["channel"]=="affect"]==[("P01",0.),("P02",.5),("P03",1.)]
    assert [x["resolved_time"] for x in a] == [x["resolved_time"] for x in b]
    phrase_timing = json.loads(Path(result["artifacts"]["conversation_phrase_timing"]).read_text())["phrases"]
    assert [row["phrase_id"] for row in phrase_timing] == ["P01", "P02", "P03"]
    assert all(row["canonical_end"] == phrase_timing[index + 1]["canonical_start"] for index, row in enumerate(phrase_timing[:-1]))
    assert result["full_script_source"]=="script.txt" and result["performance_plan_source"]==str(plan)
    assert "<mask=Friendly-50> one </mask=Friendly-50>" in Path(result["artifacts"]["characters"]["AGNES"]["jali_speaker_annotated"]).read_text()
    assert "Thinking-40" in Path(result["artifacts"]["characters"]["WILL"]["jali_speaker_annotated"]).read_text()


def test_all_channels_and_actors_receive_one_canonical_phrase_interval(tmp_path):
    plan, audio = _fixture(tmp_path)
    payload = json.loads(plan.read_text())
    for phrase in payload["phrases"]:
        for alias in ("A", "B"):
            phrase["states"][alias].update({"affect": "Friendly-50", "heart": "Happy-10", "gaze": f"GAZE-{'B' if alias == 'A' else 'A'}", "head": "LOW", "lid": -1, "blink": "DOUBLE_BLINK", "blink_suppression": "SUPPRESS"})
    plan.write_text(json.dumps(payload))
    result = compile_dual_performance_plan(performance_plan_path=plan, script=SCRIPT, audio_folder=audio, fps=24, runtime_mapping=MAPPING, output_dir=tmp_path / "out")
    actor_events = {name: json.loads(Path(result["artifacts"]["characters"][name]["semantic_events"]).read_text())["events"] for name in ("AGNES", "WILL")}
    for phrase_id in ("P01", "P02", "P03"):
        times = [[event["resolved_time"] for event in actor_events[name] if event["phrase_id"] == phrase_id] for name in ("AGNES", "WILL")]
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

def test_compiler_prefers_explicit_original_transcript_and_never_modifies_it(tmp_path):
    plan, audio = _fixture(tmp_path); originals=tmp_path/"originals"; originals.mkdir()
    source=originals/"agnes_original.txt"; source.write_text("one three",encoding="utf-8")
    mapping={**MAPPING,"A":{**MAPPING["A"],"transcript_path":str(source)}}
    (audio/"SeqT_AGNES.txt").write_text("wrong words",encoding="utf-8")
    first=compile_dual_performance_plan(performance_plan_path=plan,script=SCRIPT,audio_folder=audio,fps=24,runtime_mapping=mapping,output_dir=tmp_path/"out1")
    second=compile_dual_performance_plan(performance_plan_path=plan,script=SCRIPT,audio_folder=audio,fps=24,runtime_mapping=mapping,output_dir=tmp_path/"out2")
    assert source.read_text(encoding="utf-8") == "one three"
    assert Path(first["artifacts"]["characters"]["AGNES"]["jali_speaker_annotated"]).read_text() == Path(second["artifacts"]["characters"]["AGNES"]["jali_speaker_annotated"]).read_text()


def test_v1_compiler_uses_actual_names_and_mask_only_artifacts(tmp_path):
    plan, audio = _fixture(tmp_path)
    from expregaze_jali.dual_performance_plan_from_proposal import adapt_dual_performance_plan_v0
    payload = adapt_dual_performance_plan_v0(json.loads(plan.read_text()))
    for phrase in payload["phrases"]:
        for name in payload["characters"]:
            phrase["states"][name].pop("heart", None)
    plan.write_text(json.dumps(payload))
    mapping = {"AGNES": MAPPING["A"], "WILL": MAPPING["B"]}
    result = compile_dual_performance_plan(performance_plan_path=plan, script=SCRIPT, audio_folder=audio, fps=24, runtime_mapping=mapping, output_dir=tmp_path / "out")
    assert result["schema_version"] == "dual_animation_manifest_v1"
    assert result["characters"] == ["AGNES", "WILL"]
    assert set(result["character_runtime_mapping"]) == {"AGNES", "WILL"}
    timing = json.loads(Path(result["artifacts"]["conversation_phrase_timing"]).read_text())["phrases"]
    assert {row["speaker"] for row in timing} == {"AGNES", "WILL"}
    events = json.loads(Path(result["artifacts"]["characters"]["AGNES"]["semantic_events"]).read_text())["events"]
    assert all(event["actor"] == "AGNES" and event["channel"] != "heart" for event in events)
    annotation = Path(result["artifacts"]["characters"]["AGNES"]["jali_speaker_annotated"]).read_text()
    diagnostic = json.loads(Path(result["artifacts"]["characters"]["AGNES"]["jali_speaker_annotation"]).read_text())
    assert "<heart=" not in annotation and "heart_tag_count" not in diagnostic


def test_v2_resolves_independent_role_aware_events_and_persistent_affect(tmp_path):
    _legacy_plan, audio = _fixture(tmp_path)
    plan = tmp_path / "v2.json"
    plan.write_text(json.dumps({
        "schema_version": "dual_performance_plan_v2",
        "sequence_id": "v2",
        "characters": ["AGNES", "WILL"],
        "initial_states": {"AGNES": {"affect": "Neutral-60", "gaze": "GAZE-WILL"}, "WILL": {"affect": "Neutral-60", "gaze": "GAZE-AGNES"}},
        "initial_reasons": {"AGNES": "Begins attentive.", "WILL": "Begins attentive."},
        "tracks": {
            "AGNES": [
                {"event_id": "E001", "actor": "AGNES", "anchor_id": "w0001", "changes": {"affect": "Watchful-80", "gaze": "GAZE-WILL"}, "reason": "Starts watchful."},
                {"event_id": "E003", "actor": "AGNES", "anchor_id": "w0002", "changes": {"gaze": "GAZE-RIGHT"}, "reason": "Looks away while listening."},
            ],
            "WILL": [
                {"event_id": "E002", "actor": "WILL", "anchor_id": "w0001", "changes": {"affect": "Thinking-60"}, "reason": "Reacts to one."},
            ],
        },
    }), encoding="utf-8")
    mapping = {"AGNES": MAPPING["A"], "WILL": MAPPING["B"]}
    result = compile_dual_performance_plan(
        performance_plan_path=plan, script=SCRIPT, script_source="script.txt",
        audio_folder=audio, fps=24, runtime_mapping=mapping, output_dir=tmp_path / "v2out",
    )
    assert result["schema_version"] == "dual_animation_manifest_v2"
    assert "conversation_phrase_timing" not in result["artifacts"]
    agnes = json.loads(Path(result["artifacts"]["characters"]["AGNES"]["resolved_sparse_events"]).read_text())["events"]
    will = json.loads(Path(result["artifacts"]["characters"]["WILL"]["resolved_sparse_events"]).read_text())["events"]
    assert agnes[0]["timing_role"] == "SPEAK_ONSET" and agnes[0]["resolved_start"] == 0.0
    assert will[0]["timing_role"] == "LISTEN_REACTION"
    assert will[0]["raw_anchor_end"] == .2
    assert "reaction_delay_frames" not in will[0]
    assert will[0]["resolved_start"] == .2
    assert agnes[1]["timing_role"] == "LISTEN_REACTION" and agnes[1]["anchor_speaker"] == "WILL"
    annotation = Path(result["artifacts"]["characters"]["AGNES"]["jali_speaker_annotated"]).read_text()
    assert "<mask=Watchful-80> one </mask=Watchful-80> <mask=Watchful-80> three </mask=Watchful-80>" in annotation
    assert "<mask=Watchful-80> one three </mask=Watchful-80>" not in annotation
    assert "<mask=Thinking-60> two </mask=Thinking-60>" in Path(result["artifacts"]["characters"]["WILL"]["jali_speaker_annotated"]).read_text()


def test_v2_same_anchor_can_drive_independent_actor_times(tmp_path):
    _legacy_plan, audio = _fixture(tmp_path)
    plan = tmp_path / "v2.json"
    plan.write_text(json.dumps({
        "schema_version": "dual_performance_plan_v2", "characters": ["AGNES", "WILL"], "initial_states": {"AGNES": {"affect": "Neutral-60", "gaze": "GAZE-WILL"}, "WILL": {"affect": "Neutral-60", "gaze": "GAZE-AGNES"}}, "initial_reasons": {"AGNES": "Ready.", "WILL": "Ready."},
        "tracks": {
            "AGNES": [{"event_id": "E001", "actor": "AGNES", "anchor_id": "w0001", "changes": {"head": "HEAD-UP-SUBTLE"}, "reason": "Raises attention."}],
            "WILL": [{"event_id": "E002", "actor": "WILL", "anchor_id": "w0001", "changes": {"blink": "SLOW_BLINK"}, "reason": "Closes briefly."}],
        },
    }), encoding="utf-8")
    result = compile_dual_performance_plan(performance_plan_path=plan, script=SCRIPT, audio_folder=audio, fps=24, runtime_mapping={"AGNES": MAPPING["A"], "WILL": MAPPING["B"]}, output_dir=tmp_path / "out")
    rows = [json.loads(Path(result["artifacts"]["characters"][name]["resolved_sparse_events"]).read_text())["events"][0] for name in ("AGNES", "WILL")]
    assert rows[0]["resolved_start"] == 0.0
    assert rows[1]["resolved_start"] == .2


def test_v2_glance_remains_transient_but_later_gaze_updates_persistent_state(tmp_path):
    _legacy_plan, audio = _fixture(tmp_path)
    plan = tmp_path / "v2_glance_state.json"
    plan.write_text(json.dumps({
        "schema_version": "dual_performance_plan_v2", "characters": ["AGNES", "WILL"],
        "initial_states": {
            "AGNES": {"affect": "Neutral-60", "gaze": "GAZE-WILL"},
            "WILL": {"affect": "Neutral-60", "gaze": "GAZE-AGNES"},
        },
        "initial_reasons": {"AGNES": "Ready.", "WILL": "Ready."},
        "tracks": {
            "AGNES": [],
            "WILL": [
                {"event_id": "E1", "actor": "WILL", "anchor_id": "w0001", "changes": {"gaze": "GLANCE-UP_RIGHT"}, "reason": "Looks inward while listening."},
                {"event_id": "E2", "actor": "WILL", "anchor_id": "w0002", "changes": {"affect": "Sad-70"}, "reason": "The reply lands heavily."},
                {"event_id": "E3", "actor": "WILL", "anchor_id": "w0003", "changes": {"gaze": "GAZE-DOWN"}, "reason": "Settles into a lowered focus."},
            ],
        },
    }), encoding="utf-8")

    result = compile_dual_performance_plan(
        performance_plan_path=plan, script=SCRIPT, audio_folder=audio, fps=24,
        runtime_mapping={"AGNES": MAPPING["A"], "WILL": MAPPING["B"]},
        output_dir=tmp_path / "out",
    )
    events = json.loads(
        Path(result["artifacts"]["characters"]["WILL"]["resolved_sparse_events"])
        .read_text(encoding="utf-8")
    )["events"]

    assert events[0]["changes"]["gaze"] == "GLANCE-UP_RIGHT"
    assert events[0]["state_after"]["gaze"] == "GAZE-AGNES"
    assert events[1]["state_after"] == {
        "affect": "Sad-70", "gaze": "GAZE-AGNES", "head": "HEAD-NONE"
    }
    assert events[2]["state_after"]["gaze"] == "GAZE-DOWN"


def test_v2_hyphenated_anchor_consumes_split_timing_and_sparse_annotation_tokens(tmp_path):
    script = "WILL: Ready?\nAGNES: Mm-hmm.\nAGNES: Why?"
    audio = tmp_path / "audio"; audio.mkdir()
    for stem in ("SeqH_AGNES", "SeqH_WILL"):
        _wav(audio / f"{stem}.wav")
    _words(audio / "SeqH_WILL_words.jsonl", [{"word": "ready", "start": 0.0, "end": 0.4}])
    _words(audio / "SeqH_AGNES_words.jsonl", [
        {"word": "mm", "start": 1.0, "end": 1.2}, {"word": "hmm", "start": 1.2, "end": 1.5},
        {"word": "why", "start": 1.6, "end": 1.9},
    ])
    (audio / "SeqH_AGNES.txt").write_text("Mm-hmm. Why?", encoding="utf-8")
    (audio / "SeqH_WILL.txt").write_text("Ready?", encoding="utf-8")
    plan = tmp_path / "v2_hyphen.json"
    plan.write_text(json.dumps({
        "schema_version": "dual_performance_plan_v2", "characters": ["AGNES", "WILL"],
        "initial_states": {"AGNES": {"affect": "Neutral-60", "gaze": "GAZE-WILL"}, "WILL": {"affect": "Neutral-60", "gaze": "GAZE-AGNES"}},
        "initial_reasons": {"AGNES": "Begins attentive.", "WILL": "Begins attentive."},
        "tracks": {"AGNES": [{"event_id": "E001", "actor": "AGNES", "anchor_id": "w0003", "changes": {"affect": "Watchful-80"}, "reason": "The question sharpens attention."}], "WILL": []},
    }), encoding="utf-8")
    result = compile_dual_performance_plan(
        performance_plan_path=plan, script=script, audio_folder=audio, fps=24,
        runtime_mapping={"AGNES": {"script_name": "AGNES", "sound_file": "SeqH_AGNES"}, "WILL": {"script_name": "WILL", "sound_file": "SeqH_WILL"}}, output_dir=tmp_path / "out",
    )
    timing = json.loads(Path(result["artifacts"]["conversation_anchor_timing"]).read_text())
    assert timing["w0002"]["text"] == "Mm-hmm." and (timing["w0002"]["start"], timing["w0002"]["end"]) == (1.0, 1.5)
    assert timing["w0003"]["text"] == "Why?" and (timing["w0003"]["start"], timing["w0003"]["end"]) == (1.6, 1.9)
    annotation = Path(result["artifacts"]["characters"]["AGNES"]["jali_speaker_annotated"]).read_text(encoding="utf-8")
    diagnostic = json.loads(Path(result["artifacts"]["characters"]["AGNES"]["jali_speaker_annotation"]).read_text())
    visible = re.sub(r"</?(?:mask|heart)=[^>]+>", "", annotation)
    assert re.sub(r"\s+([?.])", r"\1", " ".join(visible.split())) == "Mm-hmm. Why?"
    assert diagnostic["anchor_token_spans"] == [[0, 2], [2, 3]]
    assert diagnostic["events"][0]["span"] == {"start": 0, "end": 6}


def test_v2_compiler_rejects_manual_avert_bypass():
    model = build_conversation_anchor_model("AGNES: one\nWILL: two", character_a="AGNES", character_b="WILL")
    plan = {"characters": ["AGNES", "WILL"], "initial_states": {"AGNES": {"affect": "Neutral-60", "gaze": "GAZE-WILL"}, "WILL": {"affect": "Neutral-60", "gaze": "GAZE-AGNES"}}, "initial_reasons": {"AGNES": "Ready.", "WILL": "Ready."}, "tracks": {"AGNES": [{"event_id": "E1", "actor": "AGNES", "anchor_id": "w0001", "changes": {"gaze": "AVERT-RIGHT"}, "reason": "Bad."}], "WILL": []}}
    with pytest.raises(ValueError, match="invalid v2 executable gaze"):
        _validate_v2_plan(plan, model)


def test_v2_compiler_rejects_none_duplicate_channels_and_invalid_blink_hold_order():
    model = build_conversation_anchor_model("AGNES: one two\nWILL: three", character_a="AGNES", character_b="WILL")
    base = {"characters": ["AGNES", "WILL"], "initial_states": {"AGNES": {"affect": "Neutral-60", "gaze": "GAZE-WILL"}, "WILL": {"affect": "Neutral-60", "gaze": "GAZE-AGNES"}}, "initial_reasons": {"AGNES": "Ready.", "WILL": "Ready."}, "tracks": {"AGNES": [], "WILL": []}}
    none = {**base, "tracks": {"AGNES": [{"event_id": "E1", "actor": "AGNES", "anchor_id": "w0001", "changes": {"gaze": "GLANCE-NONE"}, "reason": "Bad."}], "WILL": []}}
    with pytest.raises(ValueError, match="invalid v2 executable gaze"):
        _validate_v2_plan(none, model)
    duplicate = {**base, "tracks": {"AGNES": [{"event_id": "E1", "actor": "AGNES", "anchor_id": "w0001", "changes": {"gaze": "GAZE-WILL"}, "reason": "One."}, {"event_id": "E2", "actor": "AGNES", "anchor_id": "w0001", "changes": {"head": "HEAD-UP-SUBTLE"}, "reason": "Two."}], "WILL": [{"event_id": "E3", "actor": "WILL", "anchor_id": "w0001", "changes": {"head": "HEAD-NONE"}, "reason": "Independent actor."}]}}
    with pytest.raises(ValueError, match="duplicate v2 event"):
        _validate_v2_plan(duplicate, model)
    invalid_hold = {**base, "tracks": {"AGNES": [{"event_id": "E1", "actor": "AGNES", "anchor_id": "w0001", "changes": {"blink": "EYE_OPEN"}, "reason": "Bad open."}], "WILL": []}}
    with pytest.raises(ValueError, match="requires an active"):
        _validate_v2_plan(invalid_hold, model)
    valid_hold = {**base, "tracks": {"AGNES": [{"event_id": "E1", "actor": "AGNES", "anchor_id": "w0001", "changes": {"blink": "EYE_CLOSE_HOLD"}, "reason": "Close."}, {"event_id": "E2", "actor": "AGNES", "anchor_id": "w0002", "changes": {"blink": "EYE_OPEN"}, "reason": "Open."}], "WILL": []}}
    _validate_v2_plan(valid_hold, model)


def test_v2_compiler_allows_nullable_reasons_but_rejects_reserved_target():
    model = build_conversation_anchor_model("AGNES: one two\nWILL: three", character_a="AGNES", character_b="WILL")
    base = {"characters": ["AGNES", "WILL"], "initial_states": {"AGNES": {"affect": "Neutral-60", "gaze": "GAZE-WILL"}, "WILL": {"affect": "Neutral-60", "gaze": "GAZE-AGNES"}}, "initial_reasons": {"AGNES": None, "WILL": ""}, "tracks": {"AGNES": [{"event_id": "E1", "actor": "AGNES", "anchor_id": "w0001", "changes": {"head": "HEAD-UP-SUBTLE"}, "reason": None}], "WILL": []}}
    _validate_v2_plan(base, model)
    initial_target = {**base, "initial_states": {**base["initial_states"], "AGNES": {"affect": "Neutral-60", "gaze": "GAZE-target"}}}
    with pytest.raises(ValueError, match="invalid v2 initial gaze"):
        _validate_v2_plan(initial_target, model)
    event_target = {**base, "tracks": {"AGNES": [{**base["tracks"]["AGNES"][0], "changes": {"gaze": "GLANCE-TARGET"}}], "WILL": []}}
    with pytest.raises(ValueError, match="invalid v2 executable gaze"):
        _validate_v2_plan(event_target, model)


def test_v2_initial_state_and_real_listener_cue_compile_before_next_line(tmp_path):
    script = (
        "ALICE: Evening, ma'am. We're in pursuit of someone very dangerous.\n"
        "ALICE: He might have come onto your property.\n"
        "ALICE: Have you seen anyone recently?\n"
        "BOB: No.\nBOB: Bert!"
    )
    model = build_conversation_anchor_model(script, character_a="ALICE", character_b="BOB")
    anchor_times = {}
    for index, anchor in enumerate(model.anchors):
        anchor_times[anchor.anchor_id] = {"speaker": anchor.speaker, "text": anchor.text, "start": index * .2, "end": index * .2 + .1, "timing_source": "fixture"}
    by_text = {anchor.text: anchor.anchor_id for anchor in model.anchors}
    plan = {
        "schema_version": "dual_performance_plan_v2", "characters": ["ALICE", "BOB"],
        "initial_states": {
            "ALICE": {"affect": "Watchful-80", "gaze": "GAZE-BOB", "head": "HEAD-NONE"},
            "BOB": {"affect": "Watchful-85", "gaze": "GAZE-ALICE", "head": "HEAD-NONE"},
        },
        "initial_reasons": {"ALICE": "Begins watchful.", "BOB": "Begins watchful."},
        "tracks": {"ALICE": [], "BOB": [
            {"event_id": "E1", "actor": "BOB", "anchor_id": by_text["dangerous."], "changes": {"gaze": "GAZE-DOWN"}, "reason": "The threat cue changes her listening behavior."},
            {"event_id": "E2", "actor": "BOB", "anchor_id": by_text["No."], "changes": {"head": "HEAD-DOWN-SUBTLE"}, "reason": "Contains the denial."},
            {"event_id": "E3", "actor": "BOB", "anchor_id": by_text["Bert!"], "changes": {"gaze": "GAZE-RIGHT", "head": "HEAD-UP-MEDIUM"}, "reason": "Redirects attention."},
        ]},
    }
    audio = tmp_path / "audio"; audio.mkdir()
    mapping = {}
    wavs = {}
    for actor in ("ALICE", "BOB"):
        source = audio / f"{actor}.txt"
        source.write_text(" ".join(anchor.text for anchor in model.anchors if anchor.speaker == actor), encoding="utf-8")
        wav = audio / f"{actor}.wav"; wav.write_bytes(b"fixture")
        mapping[actor] = {"script_name": actor, "sound_file": actor, "transcript_path": str(source)}
        wavs[actor] = (wav, 10.0)
    out = tmp_path / "out"; out.mkdir()
    result = _compile_v2(plan=plan, model=model, anchor_times=anchor_times, mapping=mapping, audio_folder=audio, fps=24, out=out, performance_plan_path=tmp_path / "plan.json", script_source="script.txt", wavs=wavs, shared_duration=10.0, duration_warning="")
    bob_artifacts = result["artifacts"]["characters"]["BOB"]
    payload = json.loads(Path(bob_artifacts["resolved_sparse_events"]).read_text())
    initial = payload["initial_state"]
    assert initial["timing_role"] == "INITIAL_STATE" and initial["resolved_start"] == 0.0
    assert initial["state"] == {"affect": "Watchful-85", "gaze": "GAZE-ALICE", "head": "HEAD-NONE"}
    dangerous = payload["events"][0]
    assert dangerous["anchor_id"] == by_text["dangerous."] and dangerous["timing_role"] == "LISTEN_REACTION"
    assert "reaction_delay_frames" not in dangerous
    assert dangerous["resolved_start"] == anchor_times[by_text["dangerous."]]["end"]
    assert dangerous["state_after"]["affect"] == "Watchful-85"
    assert payload["events"][1]["timing_role"] == "SPEAK_ONSET" and payload["events"][1]["raw_anchor_start"] == anchor_times[by_text["No."]]["start"]
    assert payload["events"][2]["timing_role"] == "SPEAK_ONSET"
    timeline = json.loads(Path(bob_artifacts["state_timeline"]).read_text())
    assert timeline["initial_state"]["affect"] == "Watchful-85" and timeline["initial_timing"] == {"timing_role": "INITIAL_STATE", "resolved_start": 0.0}
    annotation = Path(bob_artifacts["jali_speaker_annotated"]).read_text()
    assert "<mask=Watchful-85> No </mask=Watchful-85>" in annotation
    assert "<mask=Watchful-85> Bert </mask=Watchful-85>" in annotation
