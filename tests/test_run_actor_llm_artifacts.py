from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import expregaze_jali.run_actor_llm as actor_llm


def test_general_text_artifact_helper_calls_model_once_and_uses_proposal_metadata(
    tmp_path: Path, monkeypatch
):
    calls: list[str] = []
    response = SimpleNamespace(
        id="response-1", model="test-model", output_text="[ANALYZE]\na\n[PERFORMANCE]\np\n[REASONS]\nr",
        status="completed", usage=None, incomplete_details=None, created_at=None, output=[],
    )
    monkeypatch.setattr(actor_llm, "_load_llm_config", lambda _path: {"model": "test-model"})
    monkeypatch.setattr(
        actor_llm,
        "_call_openai",
        lambda prompt, _config: calls.append(prompt) or response,
    )
    output = tmp_path / "performance_proposal.txt"
    meta_path = tmp_path / "meta.json"
    actor_llm.generate_text_artifacts(
        prompt="prompt",
        llm_config_path=tmp_path / "unused.yaml",
        prompt_path=tmp_path / "prompt.txt",
        output_text=output,
        output_meta=meta_path,
        required_sections=("[ANALYZE]", "[PERFORMANCE]", "[REASONS]"),
        artifact_name="proposal",
    )

    assert calls == ["prompt"]
    assert output.read_text(encoding="utf-8") == response.output_text
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["proposal_path"] == str(output)
    assert "annotation_path" not in meta


def test_legacy_annotation_wrapper_keeps_legacy_sections_and_metadata(tmp_path: Path, monkeypatch):
    response = SimpleNamespace(
        id="legacy-1", output_text="[ANALYZE]\na\n[ANNOTATION]\nx\n[REASONS]\nr",
        status="completed", usage=None, incomplete_details=None, created_at=None, output=[],
    )
    monkeypatch.setattr(actor_llm, "_load_llm_config", lambda _path: {"model": "test-model"})
    monkeypatch.setattr(actor_llm, "_call_openai", lambda _prompt, _config: response)
    output = tmp_path / "annotation.txt"
    meta_path = tmp_path / "meta.json"
    actor_llm.generate_actor_annotation_artifacts(
        prompt="legacy",
        llm_config_path=tmp_path / "unused.yaml",
        prompt_path=tmp_path / "prompt.txt",
        output_annotation=output,
        output_meta=meta_path,
    )

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["annotation_path"] == str(output)
    assert "output_path" not in meta
