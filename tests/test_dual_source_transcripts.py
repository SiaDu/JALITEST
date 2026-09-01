from pathlib import Path
import sys
import pytest

MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
sys.path.insert(0, str(MAYA_TOOLS))
from dual_source_transcripts import (  # noqa: E402
    export_dual_source_transcripts,
    extract_speaker_dialogue,
    resolve_character_wav,
    resolve_dual_master_wav,
)


def test_wav_matching_exact_then_suffix_and_ambiguous(tmp_path):
    (tmp_path / "AGNES.wav").write_bytes(b""); (tmp_path / "SeqT_WILL.wav").write_bytes(b"")
    assert resolve_character_wav(tmp_path, "agnes").name == "AGNES.wav"
    assert resolve_character_wav(tmp_path, "WILL").name == "SeqT_WILL.wav"
    (tmp_path / "Other_WILL.wav").write_bytes(b"")
    with pytest.raises(ValueError, match="Ambiguous WAVs"):
        resolve_character_wav(tmp_path, "WILL")
    with pytest.raises(ValueError, match="No WAV"):
        resolve_character_wav(tmp_path, "MISSING")


def test_extract_and_export_clean_speaker_txt(tmp_path):
    (tmp_path / "SeqT_AGNES.wav").write_bytes(b""); (tmp_path / "SeqT_WILL.wav").write_bytes(b"")
    script = "AGNES: Good day, sir.\nWILL: I'm tutoring the boys here.\nAGNES: What brings you?"
    assert extract_speaker_dialogue(script, ["AGNES", "WILL"])["AGNES"] == ["Good day, sir.", "What brings you?"]
    result = export_dual_source_transcripts(script=script, audio_folder=tmp_path, characters=["AGNES", "WILL"])
    assert Path(result["AGNES"]["txt"]).name == "SeqT_AGNES.txt"
    assert Path(result["AGNES"]["txt"]).read_text() == "Good day, sir.\nWhat brings you?\n"
    assert Path(result["WILL"]["txt"]).read_text() == "I'm tutoring the boys here.\n"
    assert "<mask" not in Path(result["AGNES"]["txt"]).read_text()


def test_master_wav_resolves_standard_three_file_folder(tmp_path):
    for name in ("SeqT_AGNES.wav", "SeqT_WILL.wav", "SeqT.wav"):
        (tmp_path / name).write_bytes(b"wav")

    assert resolve_dual_master_wav(tmp_path, ["AGNES", "WILL"]).name == "SeqT.wav"


def test_master_wav_is_case_insensitive_top_level_and_handles_multiword_names(tmp_path):
    (tmp_path / "Seq_MARY_JANE.WAV").write_bytes(b"actor")
    (tmp_path / "Seq_DR-WHO.wav").write_bytes(b"actor")
    (tmp_path / "Seq_Mary-Jane_alt.wav").write_bytes(b"excluded by character token")
    (tmp_path / "Complete_Mix.WaV").write_bytes(b"master")
    nested = tmp_path / "nested"; nested.mkdir()
    (nested / "Nested.wav").write_bytes(b"ignored")

    assert resolve_character_wav(tmp_path, "mary jane").name == "Seq_MARY_JANE.WAV"
    assert resolve_character_wav(tmp_path, "Dr Who").name == "Seq_DR-WHO.wav"
    assert resolve_dual_master_wav(tmp_path, ["Mary Jane", "DR WHO"]).name == "Complete_Mix.WaV"


def test_master_wav_excludes_explicit_character_paths_and_delimited_name_matches(tmp_path):
    actor_a = tmp_path / "take_one.wav"; actor_a.write_bytes(b"a")
    actor_b = tmp_path / "take_two.wav"; actor_b.write_bytes(b"b")
    (tmp_path / "scene-ANN-extra.wav").write_bytes(b"excluded")
    master = tmp_path / "scene.wav"; master.write_bytes(b"master")

    assert resolve_dual_master_wav(
        tmp_path,
        ["ANN", "BOB"],
        character_wavs=[actor_a, actor_b],
    ) == master.resolve()


def test_master_wav_requires_exactly_one_candidate_and_lists_candidates(tmp_path):
    for name in ("SeqT_AGNES.wav", "SeqT_WILL.wav"):
        (tmp_path / name).write_bytes(b"actor")
    with pytest.raises(ValueError, match=r"found 0.*Candidates: none.*SeqT_AGNES\.wav"):
        resolve_dual_master_wav(tmp_path, ["AGNES", "WILL"])

    (tmp_path / "SeqT.wav").write_bytes(b"one")
    (tmp_path / "SeqT_full.wav").write_bytes(b"two")
    with pytest.raises(ValueError, match=r"found 2.*SeqT\.wav.*SeqT_full\.wav"):
        resolve_dual_master_wav(tmp_path, ["AGNES", "WILL"])
