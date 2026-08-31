from __future__ import annotations

from pathlib import Path
import sys

import pytest


MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
if str(MAYA_TOOLS) not in sys.path:
    sys.path.insert(0, str(MAYA_TOOLS))

from jali_speech_base import (  # noqa: E402
    ensure_dual_jali_speech_bases,
    ensure_jali_speech_base,
    ensure_jali_runtime_available,
    inspect_jali_speech_base,
    prepare_jali_speech_base,
    speech_base_status_text,
    transcript_sha256,
)


class FakeCmds:
    def __init__(self, rigs=("|AGNES|JALI_GRP", "|WILL|JALI_GRP")):
        self.nodes = set(rigs)
        self.values: dict[str, object] = {}
        self.selection = ["prop_CTRL"]
        self.select_calls: list[tuple[object, dict]] = []

    def add_jsync(self, rig: str, node_name: str, sound: str, txt: Path) -> str:
        node = f"{rig}|speech|{node_name}"
        self.nodes.add(node)
        self.values[f"{node}.sound_file"] = sound
        self.values[f"{node}.text_input_path"] = str(txt.parent)
        self.values[f"{node}.transcript"] = txt.read_text(encoding="utf-8")
        self.values[f"{node}.sound_file_format"] = ".wav"
        self.values[f"{node}.output_path"] = str(txt.parent)
        (txt.parent / f"{sound}_PraatOutput.txt").write_text(
            "alignment", encoding="utf-8"
        )
        return node

    def objExists(self, item):
        return (
            item in self.nodes
            or item in self.values
            or any(node.rsplit("|", 1)[-1] == item for node in self.nodes)
        )

    def ls(self, *items, **kwargs):
        if kwargs.get("type") == "jSync":
            return sorted(node for node in self.nodes if node.rsplit("|", 1)[-1].startswith("jSync"))
        if kwargs.get("selection"):
            return list(self.selection)
        if items:
            requested = str(items[0])
            exact = [node for node in self.nodes if node == requested]
            if exact:
                return exact
            return [node for node in self.nodes if node.rsplit("|", 1)[-1] == requested]
        return []

    def getAttr(self, plug):
        return self.values[plug]

    def setAttr(self, plug, value, **_kwargs):
        self.values[plug] = value

    def select(self, items=None, **kwargs):
        self.select_calls.append((items, kwargs))
        if kwargs.get("clear"):
            self.selection = []
        elif isinstance(items, str):
            self.selection = [items]
        else:
            self.selection = list(items or [])


class FakeMel:
    def __init__(self, *, available=True, on_call=None, fail_call=False):
        self.available = available
        self.on_call = on_call
        self.fail_call = fail_call
        self.calls: list[str] = []

    def eval(self, command):
        self.calls.append(command)
        if command == 'exists "call_jSync"':
            return int(self.available)
        if command.startswith("source "):
            return None
        if command == "JaliMayaStart(0);":
            self.available = True
            return None
        if command.startswith("call_jSync"):
            if self.fail_call:
                raise RuntimeError("alignment failed")
            if self.on_call:
                self.on_call()
            return None
        return None


def sources(tmp_path: Path):
    result = {}
    for actor in ("AGNES", "WILL"):
        wav = tmp_path / f"SeqT_{actor}.wav"; wav.write_bytes(b"RIFF")
        txt = tmp_path / f"SeqT_{actor}.txt"; txt.write_text(actor + " line\n", encoding="utf-8")
        result[actor] = {"wav": str(wav), "txt": str(txt)}
    return result


def mappings():
    return {
        "AGNES": {"script_name": "AGNES", "maya_node": "|AGNES|JALI_GRP"},
        "WILL": {"script_name": "WILL", "maya_node": "|WILL|JALI_GRP"},
    }


def config(tmp_path: Path) -> Path:
    path = tmp_path / "maya.yaml"
    path.write_text("maya_jali_speech_base:\n  language_code: 0\n  speech_style: 0\n", encoding="utf-8")
    return path


def metadata(actor: str, rig: str, jsync: str, source: dict[str, str]) -> dict[str, object]:
    wav, txt = Path(source["wav"]).resolve(), Path(source["txt"]).resolve()
    return {
        "script_name": actor, "maya_node": rig, "jsync": jsync,
        "sound_file": wav.stem, "wav_path": str(wav), "txt_path": str(txt),
        "txt_sha256": transcript_sha256(txt), "preparation_status": "prepared",
        "wav_size": wav.stat().st_size, "wav_mtime_ns": wav.stat().st_mtime_ns,
        "prepared_at": "2026-08-31T00:00:00+00:00",
    }


def test_existing_exact_base_with_matching_fingerprint_is_reused(tmp_path):
    source = sources(tmp_path)["AGNES"]; cmds = FakeCmds(); rig = mappings()["AGNES"]["maya_node"]
    jsync = cmds.add_jsync(rig, "jSync17", "SeqT_AGNES", Path(source["txt"]))
    mel = FakeMel()
    result = ensure_dual_jali_speech_bases(
        actors=("AGNES", "WILL"), character_mappings=mappings(),
        source_transcripts={"AGNES": source, "WILL": sources(tmp_path)["WILL"]},
        saved_metadata={"AGNES": metadata("AGNES", rig, jsync, source)},
        config_path=config(tmp_path), cmds_module=cmds,
        mel_module=FakeMel(on_call=lambda: cmds.add_jsync("|WILL|JALI_GRP", "jSync3", "SeqT_WILL", tmp_path / "SeqT_WILL.txt")),
    )
    assert result["AGNES"]["preparation_status"] == "reused"
    assert result["AGNES"]["jsync"].endswith("jSync17")


def test_no_existing_jsync_calls_jali_once_and_verifies_new_node(tmp_path):
    source = sources(tmp_path)["AGNES"]; cmds = FakeCmds(); calls = []
    mel = FakeMel(on_call=lambda: (calls.append("call"), cmds.add_jsync("|AGNES|JALI_GRP", "jSync17", "SeqT_AGNES", Path(source["txt"]))))
    result = prepare_jali_speech_base(
        actor="AGNES", script_name="AGNES", maya_node="|AGNES|JALI_GRP",
        wav_path=source["wav"], txt_path=source["txt"], language_code=0,
        speech_style=0, known_mapped_rigs=("|AGNES|JALI_GRP", "|WILL|JALI_GRP"),
        cmds_module=cmds, mel_module=mel,
    )
    assert calls == ["call"]
    assert result["preparation_status"] == "prepared"
    assert result["jsync"].endswith("jSync17")
    assert cmds.selection == ["prop_CTRL"]
    command = next(item for item in mel.calls if item.startswith("call_jSync"))
    assert '/", "' in command


def test_short_maya_mapping_is_normalized_to_long_dag_path(tmp_path):
    source = sources(tmp_path)["AGNES"]
    cmds = FakeCmds(rigs=("|AGNES:JALI_GRP", "|WILL:JALI_GRP"))
    jsync = cmds.add_jsync(
        "|AGNES:JALI_GRP", "jSync17", "SeqT_AGNES", Path(source["txt"])
    )
    inspected = inspect_jali_speech_base(
        actor="AGNES",
        script_name="AGNES",
        maya_node="AGNES:JALI_GRP",
        wav_path=source["wav"],
        txt_path=source["txt"],
        cmds_module=cmds,
    )
    assert inspected["reusable"] is True
    assert inspected["maya_node"] == "|AGNES:JALI_GRP"
    assert inspected["jsync"] == jsync


def test_wrong_rig_creation_hard_fails_with_discovered_nodes(tmp_path):
    source = sources(tmp_path)["AGNES"]; cmds = FakeCmds()
    mel = FakeMel(on_call=lambda: cmds.add_jsync("|WILL|JALI_GRP", "jSync3", "SeqT_AGNES", Path(source["txt"])))
    with pytest.raises(RuntimeError, match="expected rig.*WILL"):
        prepare_jali_speech_base(
            actor="AGNES", script_name="AGNES", maya_node="|AGNES|JALI_GRP",
            wav_path=source["wav"], txt_path=source["txt"], language_code=0,
            speech_style=0, known_mapped_rigs=("|AGNES|JALI_GRP", "|WILL|JALI_GRP"),
            cmds_module=cmds, mel_module=mel,
        )


def test_old_unrelated_jsync_is_ignored_but_two_matching_are_ambiguous(tmp_path):
    source = sources(tmp_path)["AGNES"]; cmds = FakeCmds(); rig = "|AGNES|JALI_GRP"
    cmds.add_jsync(rig, "jSync1", "old_scene", Path(source["txt"]))
    correct = cmds.add_jsync(rig, "jSync17", "SeqT_AGNES", Path(source["txt"]))
    assert inspect_jali_speech_base(
        actor="AGNES", script_name="AGNES", maya_node=rig, wav_path=source["wav"],
        txt_path=source["txt"], cmds_module=cmds,
    )["jsync"] == correct
    cmds.add_jsync(rig, "jSync99", "SeqT_AGNES", Path(source["txt"]))
    with pytest.raises(RuntimeError, match="ambiguous matching"):
        inspect_jali_speech_base(
            actor="AGNES", script_name="AGNES", maya_node=rig, wav_path=source["wav"],
            txt_path=source["txt"], cmds_module=cmds,
        )


def test_changed_transcript_content_invalidates_same_path(tmp_path):
    source = sources(tmp_path)["AGNES"]; cmds = FakeCmds(); rig = "|AGNES|JALI_GRP"
    jsync = cmds.add_jsync(rig, "jSync17", "SeqT_AGNES", Path(source["txt"]))
    saved = metadata("AGNES", rig, jsync, source)
    Path(source["txt"]).write_text("changed\n", encoding="utf-8")
    inspected = inspect_jali_speech_base(
        actor="AGNES", script_name="AGNES", maya_node=rig, wav_path=source["wav"],
        txt_path=source["txt"], saved_metadata=saved, cmds_module=cmds,
    )
    assert inspected["reusable"] is False
    assert "txt_sha256" in inspected["reason"]


def test_node_identity_without_completed_native_speech_is_not_reusable(tmp_path):
    source = sources(tmp_path)["AGNES"]
    cmds = FakeCmds()
    rig = "|AGNES|JALI_GRP"
    jsync = cmds.add_jsync(rig, "jSync17", "SeqT_AGNES", Path(source["txt"]))
    cmds.values[f"{jsync}.transcript"] = ""
    inspected = inspect_jali_speech_base(
        actor="AGNES",
        script_name="AGNES",
        maya_node=rig,
        wav_path=source["wav"],
        txt_path=source["txt"],
        cmds_module=cmds,
    )
    assert inspected["reusable"] is False
    assert "no transcript" in inspected["reason"]


def test_changed_transcript_reprepares_and_keeps_old_node_as_stale(tmp_path):
    all_sources = sources(tmp_path); source = all_sources["AGNES"]; cmds = FakeCmds(); rig = "|AGNES|JALI_GRP"
    old = cmds.add_jsync(rig, "jSync1", "SeqT_AGNES", Path(source["txt"]))
    saved = metadata("AGNES", rig, old, source)
    Path(source["txt"]).write_text("changed current transcript\n", encoding="utf-8")
    calls = []
    mel = FakeMel(on_call=lambda: (calls.append("call"), cmds.add_jsync(rig, "jSync17", "SeqT_AGNES", Path(source["txt"]))))
    # Exercise the actor operation directly so the second actor is irrelevant.
    result = ensure_jali_speech_base(
        actor="AGNES", script_name="AGNES", maya_node=rig, wav_path=source["wav"],
        txt_path=source["txt"], saved_metadata=saved, language_code=0, speech_style=0,
        known_mapped_rigs=(rig, "|WILL|JALI_GRP"), cmds_module=cmds, mel_module=mel,
    )
    assert calls == ["call"]
    assert result["preparation_status"] == "prepared"
    assert result["jsync"].endswith("jSync17")
    assert old in cmds.nodes
    assert str(cmds.values[f"{old}.sound_file"]).startswith("SeqT_AGNES__JALITEST_STALE_")
    assert (tmp_path / "SeqT_AGNES_PraatOutput.txt").is_file()
    assert list(tmp_path.glob("SeqT_AGNES_PraatOutput.txt.JALITEST_STALE_*"))


def test_semantic_edits_do_not_invalidate_speech_identity(tmp_path):
    source = sources(tmp_path)["AGNES"]; cmds = FakeCmds(); rig = "|AGNES|JALI_GRP"
    jsync = cmds.add_jsync(rig, "jSync17", "SeqT_AGNES", Path(source["txt"]))
    saved = metadata("AGNES", rig, jsync, source)
    # Semantic plan fields are deliberately not inputs to speech-base inspection.
    for _semantic_edit in ({"affect": "Nervous-65"}, {"gaze": "GLANCE-DOWN"}, {"blink": "SLOW_BLINK"}, {"head": "HEAD-HIGH"}):
        assert inspect_jali_speech_base(
            actor="AGNES", script_name="AGNES", maya_node=rig, wav_path=source["wav"],
            txt_path=source["txt"], saved_metadata=saved, cmds_module=cmds,
        )["reusable"] is True


def test_wav_stat_change_invalidates_saved_identity(tmp_path):
    source = sources(tmp_path)["AGNES"]; cmds = FakeCmds(); rig = "|AGNES|JALI_GRP"
    jsync = cmds.add_jsync(rig, "jSync17", "SeqT_AGNES", Path(source["txt"]))
    saved = metadata("AGNES", rig, jsync, source)
    Path(source["wav"]).write_bytes(b"RIFF changed")
    inspected = inspect_jali_speech_base(
        actor="AGNES", script_name="AGNES", maya_node=rig, wav_path=source["wav"],
        txt_path=source["txt"], saved_metadata=saved, cmds_module=cmds,
    )
    assert inspected["reusable"] is False
    assert "wav_size" in inspected["reason"]


def test_dual_resolution_does_not_depend_on_jsync_numbering(tmp_path):
    all_sources = sources(tmp_path); cmds = FakeCmds()
    agnes = cmds.add_jsync("|AGNES|JALI_GRP", "jSync17", "SeqT_AGNES", Path(all_sources["AGNES"]["txt"]))
    will = cmds.add_jsync("|WILL|JALI_GRP", "jSync3", "SeqT_WILL", Path(all_sources["WILL"]["txt"]))
    saved = {
        "AGNES": metadata("AGNES", "|AGNES|JALI_GRP", agnes, all_sources["AGNES"]),
        "WILL": metadata("WILL", "|WILL|JALI_GRP", will, all_sources["WILL"]),
    }
    result = ensure_dual_jali_speech_bases(
        actors=("AGNES", "WILL"), character_mappings=mappings(),
        source_transcripts=all_sources, saved_metadata=saved,
        config_path=config(tmp_path), cmds_module=cmds, mel_module=FakeMel(),
    )
    assert result["AGNES"]["jsync"].endswith("jSync17")
    assert result["WILL"]["jsync"].endswith("jSync3")
    assert {row["preparation_status"] for row in result.values()} == {"reused"}


def test_startup_existing_source_success_and_still_missing_failure(tmp_path):
    already = FakeMel(available=True)
    assert ensure_jali_runtime_available(mel_module=already) is None
    assert already.calls == ['exists "call_jSync"']
    startup = tmp_path / "scripts" / "JaliMayaStart.mel"; startup.parent.mkdir(); startup.write_text("// test")
    missing = FakeMel(available=False)
    assert ensure_jali_runtime_available(mel_module=missing, install_path=tmp_path) == startup
    assert any(call.startswith("source ") for call in missing.calls)
    class NeverAvailable(FakeMel):
        def eval(self, command):
            self.calls.append(command)
            return 0 if command == 'exists "call_jSync"' else None
    with pytest.raises(RuntimeError, match="call_jSync is not loaded"):
        ensure_jali_runtime_available(mel_module=NeverAvailable(available=False), install_path=tmp_path)


def test_selection_restored_after_failed_preparation(tmp_path):
    source = sources(tmp_path)["AGNES"]; cmds = FakeCmds(); mel = FakeMel(fail_call=True)
    with pytest.raises(RuntimeError, match="alignment failed"):
        prepare_jali_speech_base(
            actor="AGNES", script_name="AGNES", maya_node="|AGNES|JALI_GRP",
            wav_path=source["wav"], txt_path=source["txt"], language_code=0,
            speech_style=0, known_mapped_rigs=("|AGNES|JALI_GRP", "|WILL|JALI_GRP"),
            cmds_module=cmds, mel_module=mel,
        )
    assert cmds.selection == ["prop_CTRL"]


def test_failed_call_preserves_but_retires_new_partial_jsync(tmp_path):
    source = sources(tmp_path)["AGNES"]
    cmds = FakeCmds()

    def create_then_fail():
        cmds.add_jsync(
            "|AGNES|JALI_GRP", "jSync17", "SeqT_AGNES", Path(source["txt"])
        )
        raise RuntimeError("alignment failed after node creation")

    with pytest.raises(RuntimeError, match="after node creation"):
        prepare_jali_speech_base(
            actor="AGNES",
            script_name="AGNES",
            maya_node="|AGNES|JALI_GRP",
            wav_path=source["wav"],
            txt_path=source["txt"],
            language_code=0,
            speech_style=0,
            known_mapped_rigs=("|AGNES|JALI_GRP", "|WILL|JALI_GRP"),
            cmds_module=cmds,
            mel_module=FakeMel(on_call=create_then_fail),
        )
    partial = "|AGNES|JALI_GRP|speech|jSync17"
    assert partial in cmds.nodes
    assert str(cmds.values[f"{partial}.sound_file"]).startswith(
        "SeqT_AGNES__JALITEST_FAILED_"
    )
    assert cmds.selection == ["prop_CTRL"]


@pytest.mark.parametrize(
    "status",
    ["will_prepare", "preparing", "reused", "prepared", "failed", "not_started"],
)
def test_participant_status_helper_is_concise(status):
    text = speech_base_status_text("AGNES", "SeqT_AGNES", status)
    assert "AGNES" in text and "SeqT_AGNES" in text
    assert "call_jSync" not in text and "MEL" not in text


def test_first_actor_failure_marks_second_actor_not_started(tmp_path):
    all_sources = sources(tmp_path)
    statuses = []
    with pytest.raises(RuntimeError, match="alignment failed"):
        ensure_dual_jali_speech_bases(
            actors=("AGNES", "WILL"),
            character_mappings=mappings(),
            source_transcripts=all_sources,
            config_path=config(tmp_path),
            cmds_module=FakeCmds(),
            mel_module=FakeMel(fail_call=True),
            status_callback=lambda actor, clip, status: statuses.append(
                (actor, clip, status)
            ),
        )
    assert ("AGNES", "SeqT_AGNES", "failed") in statuses
    assert ("WILL", "SeqT_WILL", "not_started") in statuses
