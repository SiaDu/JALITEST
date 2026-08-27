from pathlib import Path
import sys
import pytest

MAYA_TOOLS = Path(__file__).resolve().parents[1] / "tools" / "maya"
sys.path.insert(0, str(MAYA_TOOLS))
from dual_source_transcripts import export_dual_source_transcripts, extract_speaker_dialogue, resolve_character_wav  # noqa: E402


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
