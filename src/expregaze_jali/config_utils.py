from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


DEFAULT_PROJECT_CONFIG = Path("configs/project.yaml")
DEFAULT_SEQUENCE_CONFIG = Path("configs/sequences/Jali_proto_candidate_001_ProfessorCrystal.yaml")
DEFAULT_LLM_CONFIG = Path("configs/llm.yaml")


def read_yaml(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Config file not found: {file_path}")
    data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {file_path}")
    return data


def _as_mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Config field `{name}` must be a mapping")
    return value


def repo_root_from_project_config(project_config_path: str | Path, project_config: dict[str, Any]) -> Path:
    config_path = Path(project_config_path)
    project = _as_mapping(project_config.get("project"), "project")
    raw_root = project.get("root", ".")
    root = Path(str(raw_root))
    if root.is_absolute():
        return root
    # configs/project.yaml -> repo root is configs/..
    return (config_path.parent.parent / root).resolve()


def resolve_repo_path(path_value: str | Path | None, repo_root: str | Path) -> Path | None:
    if path_value is None or str(path_value).strip() == "":
        return None
    path = Path(str(path_value))
    return path if path.is_absolute() else Path(repo_root) / path


def resolve_existing_path(path_value: str | Path | None, base_dir: str | Path) -> Path | None:
    path = resolve_repo_path(path_value, base_dir)
    if path is None:
        return None
    if path.exists():
        return path

    # Allow Windows-style paths while running under WSL/Linux.
    text = str(path_value)
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if os.name != "nt" and match:
        wsl_path = Path("/mnt") / match.group(1).lower() / match.group(2).replace("\\", "/")
        if wsl_path.exists():
            return wsl_path
    return path


def sequence_section(sequence_config: dict[str, Any]) -> dict[str, Any]:
    section = sequence_config.get("sequence", sequence_config)
    return _as_mapping(section, "sequence")


def jali_section(sequence_config: dict[str, Any]) -> dict[str, Any]:
    return _as_mapping(sequence_config.get("jali"), "jali")


def sequence_id_from_config(sequence_config: dict[str, Any]) -> str:
    seq = sequence_section(sequence_config)
    jali = jali_section(sequence_config)
    value = seq.get("sequence_id") or jali.get("clip_name")
    if not value:
        raise ValueError("Sequence config must define sequence.sequence_id or jali.clip_name")
    return str(value)


def clip_name_from_config(sequence_config: dict[str, Any]) -> str:
    jali = jali_section(sequence_config)
    return str(jali.get("clip_name") or sequence_id_from_config(sequence_config))


def movie_id_from_config(sequence_config: dict[str, Any]) -> str | None:
    value = sequence_section(sequence_config).get("movie_id")
    return str(value) if value else None


def movie_name_from_config(sequence_config: dict[str, Any]) -> str | None:
    value = sequence_section(sequence_config).get("movie_name")
    return str(value) if value else None


def shot_range_from_config(sequence_config: dict[str, Any]) -> tuple[int, int]:
    seq = sequence_section(sequence_config)
    shot_range = _as_mapping(seq.get("shot_range"), "sequence.shot_range")
    start = shot_range.get("start_shot_idx")
    end = shot_range.get("end_shot_idx")
    if start is None or end is None:
        raise ValueError("Sequence config must define sequence.shot_range.start_shot_idx/end_shot_idx")
    return int(start), int(end)


def local_window_from_config(sequence_config: dict[str, Any], default: int = 3) -> int:
    value = sequence_section(sequence_config).get("local_window", default)
    return int(value)


def fps_from_config(sequence_config: dict[str, Any], default: float = 30.0) -> float:
    value = sequence_section(sequence_config).get("fps", default)
    return float(value)


def clip_end_frame_from_config(sequence_config: dict[str, Any]) -> float | None:
    value = sequence_section(sequence_config).get("clip_end_frame")
    return float(value) if value not in (None, "") else None


def full_context_path(
    project_config: dict[str, Any],
    sequence_config: dict[str, Any],
    repo_root: str | Path,
) -> Path:
    seq = sequence_section(sequence_config)
    explicit = seq.get("full_context_file") or seq.get("full_context_csv") or seq.get("full_context_path")
    if explicit:
        path = resolve_repo_path(explicit, repo_root)
        assert path is not None
        return path

    movie_id = movie_id_from_config(sequence_config)
    if not movie_id:
        raise ValueError("Cannot infer full_context path because sequence.movie_id is missing")
    data_cfg = _as_mapping(project_config.get("data"), "data")
    full_context_dir = resolve_repo_path(data_cfg.get("full_context_dir", "data/processed/full_context"), repo_root)
    assert full_context_dir is not None
    return full_context_dir / f"{movie_id}__full_context.csv"


def llm_process_dir(project_config: dict[str, Any], repo_root: str | Path) -> Path:
    data_cfg = _as_mapping(project_config.get("data"), "data")
    path = resolve_repo_path(data_cfg.get("llm_process_dir", "data/processed/gaze_script/llm_process"), repo_root)
    assert path is not None
    return path


def textgrid_output_dir(project_config: dict[str, Any], repo_root: str | Path) -> Path:
    data_cfg = _as_mapping(project_config.get("data"), "data")
    path = resolve_repo_path(data_cfg.get("textgrid_output_dir", "data/processed/textgrid"), repo_root)
    assert path is not None
    return path


def compiled_output_dir(project_config: dict[str, Any], repo_root: str | Path) -> Path:
    data_cfg = _as_mapping(project_config.get("data"), "data")
    path = resolve_repo_path(data_cfg.get("compiled_output_dir", "data/processed/gaze_script"), repo_root)
    assert path is not None
    return path


def prompt_template_path(project_config: dict[str, Any], repo_root: str | Path) -> Path:
    prompt_cfg = _as_mapping(project_config.get("prompt"), "prompt")
    path = resolve_repo_path(prompt_cfg.get("template", "prompts/actor_performance_annotation_prompt_v2.md"), repo_root)
    assert path is not None
    return path


def prompt_extra_config_paths(project_config: dict[str, Any], repo_root: str | Path) -> list[Path]:
    prompt_cfg = _as_mapping(project_config.get("prompt"), "prompt")
    values = prompt_cfg.get("extra_config_files") or [
        "configs/jali_emotion_options.yaml",
        "configs/performance_rules.yaml",
    ]
    if not isinstance(values, list):
        raise ValueError("project.prompt.extra_config_files must be a list")
    out: list[Path] = []
    for value in values:
        path = resolve_repo_path(value, repo_root)
        if path is not None:
            out.append(path)
    return out


def jali_project_root(sequence_config: dict[str, Any], repo_root: str | Path) -> Path:
    jali = jali_section(sequence_config)
    value = jali.get("project_root", repo_root)
    path = resolve_existing_path(value, repo_root)
    assert path is not None
    return path


def _jali_project_relative_path(
    sequence_config: dict[str, Any],
    repo_root: str | Path,
    explicit_key: str,
    default_suffix: str,
) -> Path:
    jali = jali_section(sequence_config)
    root = jali_project_root(sequence_config, repo_root)
    explicit = jali.get(explicit_key)
    if explicit:
        path = Path(str(explicit))
        if path.is_absolute():
            return path
        return root / path
    input_dir = str(jali.get("input_dir", ""))
    return root / input_dir / default_suffix


def transcript_path(sequence_config: dict[str, Any], repo_root: str | Path) -> Path:
    clip = clip_name_from_config(sequence_config)
    return _jali_project_relative_path(sequence_config, repo_root, "transcript_file", f"{clip}.txt")


def textgrid_path(sequence_config: dict[str, Any], repo_root: str | Path) -> Path:
    clip = clip_name_from_config(sequence_config)
    return _jali_project_relative_path(sequence_config, repo_root, "textgrid_file", f"{clip}.Textgrid")


def audio_path(sequence_config: dict[str, Any], repo_root: str | Path) -> Path:
    clip = clip_name_from_config(sequence_config)
    return _jali_project_relative_path(sequence_config, repo_root, "audio_file", f"{clip}.wav")
