"""Automatic native JALI speech-base preparation for dual authoring.

The module is intentionally independent of Qt and accepts injected Maya
``cmds``/``mel`` modules so its startup, inspection, reuse, and selection
safety can be tested outside Maya.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
from typing import Any, Callable, Iterable

from animation_apply_runner import (
    DEFAULT_MAYA_CONFIG,
    resolve_jali_source_transcript_path,
    resolve_jsync_for_character,
)


REQUIRED_JSYNC_ATTRS = (
    "sound_file",
    "text_input_path",
    "transcript",
    "sound_file_format",
    "silence_handling",
    "silence_handling_decibel",
)
STATUS_TEXT = {
    "will_prepare": "Will prepare automatically",
    "existing": "Ready - Existing",
    "existing_required": "Use existing JALI speech",
    "waiting": "Waiting...",
    "preparing": "Preparing JALI speech...",
    "reused": "Ready - Reused",
    "prepared": "Ready - Prepared",
    "failed": "Failed",
    "not_started": "Not started",
}
StatusCallback = Callable[[str, str, str], None]
DEFAULT_JALI_SPEECH_SETTINGS = {
    "filter_silence_gaps": True,
    "silence_threshold_db": -35.0,
}


def speech_base_status_text(actor: str, clip_name: str, status: str) -> str:
    """Return concise participant-facing setup state."""
    if status not in STATUS_TEXT:
        raise ValueError(f"Unknown JALI speech-base status: {status!r}")
    parts = [str(actor).strip(), str(clip_name).strip(), STATUS_TEXT[status]]
    return "   ".join(part for part in parts if part)


def transcript_sha256(path: str | Path) -> str:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"JALI transcript not found: {source}")
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _wav_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"wav_size": int(stat.st_size), "wav_mtime_ns": int(stat.st_mtime_ns)}


def normalize_jali_speech_settings(value: dict[str, Any] | None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else DEFAULT_JALI_SPEECH_SETTINGS
    filter_silence = raw.get("filter_silence_gaps", True)
    threshold = raw.get("silence_threshold_db", -35.0)
    if not isinstance(filter_silence, bool):
        raise ValueError("filter_silence_gaps must be true or false.")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("silence_threshold_db must be a number.")
    threshold = float(threshold)
    if not math.isfinite(threshold) or not -100.0 <= threshold <= 0.0:
        raise ValueError("silence_threshold_db must be between -100 and 0 dB.")
    return {
        "filter_silence_gaps": filter_silence,
        "silence_threshold_db": threshold,
    }


def load_jali_speech_base_config(config_path: str | Path = DEFAULT_MAYA_CONFIG) -> dict[str, Any]:
    # Maya's bundled Python does not include PyYAML. Reuse the repository's
    # established Maya-compatible loader instead of adding a runtime package.
    from expregaze_jali.maya_apply_gaze import _load_yaml_file
    raw = _load_yaml_file(Path(config_path))
    section = raw.get("maya_jali_speech_base") if isinstance(raw, dict) else None
    if not isinstance(section, dict):
        raise ValueError("Maya config requires a maya_jali_speech_base mapping.")
    result: dict[str, Any] = {}
    for key in ("language_code", "speech_style"):
        value = section.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"maya_jali_speech_base.{key} must be a non-negative integer.")
        result[key] = value
    result.update(normalize_jali_speech_settings(section))
    overrides = section.get("sequence_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("maya_jali_speech_base.sequence_overrides must be a mapping.")
    result["sequence_overrides"] = {
        str(sequence): normalize_jali_speech_settings({
            **{
                "filter_silence_gaps": result["filter_silence_gaps"],
                "silence_threshold_db": result["silence_threshold_db"],
            },
            **(override if isinstance(override, dict) else {}),
        })
        for sequence, override in overrides.items()
        if str(sequence).strip()
    }
    if any(not isinstance(override, dict) for override in overrides.values()):
        raise ValueError("Each JALI sequence override must be a mapping.")
    return result


def jali_speech_settings_for_audio_folder(
    audio_folder: str | Path,
    config_path: str | Path = DEFAULT_MAYA_CONFIG,
) -> dict[str, Any]:
    """Resolve editable defaults, including an exact folder-name override."""
    config = load_jali_speech_base_config(config_path)
    settings = normalize_jali_speech_settings(config)
    sequence = Path(str(audio_folder).strip()).name.casefold()
    override = next(
        (
            row
            for name, row in config["sequence_overrides"].items()
            if str(name).casefold() == sequence
        ),
        None,
    )
    return normalize_jali_speech_settings(override or settings)


def _jali_speech_settings_match(saved: Any, current: dict[str, Any]) -> bool:
    if not isinstance(saved, dict):
        return False
    try:
        old = normalize_jali_speech_settings(saved)
    except ValueError:
        return False
    if old["filter_silence_gaps"] != current["filter_silence_gaps"]:
        return False
    return (
        not current["filter_silence_gaps"]
        or old["silence_threshold_db"] == current["silence_threshold_db"]
    )


def _mel_string(value: str | Path) -> str:
    return str(value).replace("\\", "/").replace('"', '\\"')


def _mel_folder(value: str | Path) -> str:
    # JALI 2.18's call_jSync validates a normalized folder internally, but its
    # subsequent input-file lookup uses the original argument. A trailing slash
    # is therefore required for real files to resolve correctly.
    return _mel_string(value).rstrip("/") + "/"


def ensure_jali_runtime_available(
    *, mel_module: Any | None = None, install_path: str | Path | None = None,
) -> Path | None:
    """Load JALI's documented native Animate-from-File pipeline when absent."""
    if mel_module is None:
        from maya import mel as mel_module  # type: ignore
    required = ("jali_prepare_folder_for_aligning", "jAnalyze_batch", "call_jSync")
    if all(mel_module.eval(f'exists "{name}"') for name in required):
        return None
    install = Path(install_path or os.environ.get("JALI_INSTALL") or "C:/ProgramData/Jali")
    startup = install / "scripts" / "JaliMayaStart.mel"
    if not startup.is_file():
        raise RuntimeError(
            "Automatic JALI preparation is unavailable because call_jSync is not loaded "
            f"and the startup script was not found: {startup}. Check JALI_INSTALL."
        )
    mel_module.eval(f'source "{_mel_string(startup)}";')
    mel_module.eval("JaliMayaStart(0);")
    missing = [name for name in required if not mel_module.eval(f'exists "{name}"')]
    if missing:
        raise RuntimeError(
            "Automatic JALI preparation is unavailable because the native Animate from File "
            f"pipeline is not loaded ({', '.join(missing)}). "
            "Check the JALI installation/plugin and JALI_INSTALL path."
        )
    return startup


def _normalized_path(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _jsync_nodes(cmds_module: Any) -> list[str]:
    return [str(node) for node in (cmds_module.ls(type="jSync", long=True) or [])]


def _nodes_under(cmds_module: Any, rig: str) -> set[str]:
    prefix = str(rig).rstrip("|") + "|"
    return {node for node in _jsync_nodes(cmds_module) if node.startswith(prefix)}


def _matching_nodes(cmds_module: Any, rig: str, sound_file: str) -> list[str]:
    prefix = str(rig).rstrip("|") + "|"
    matches: list[str] = []
    for node in _jsync_nodes(cmds_module):
        if not node.startswith(prefix):
            continue
        plug = f"{node}.sound_file"
        if cmds_module.objExists(plug) and str(cmds_module.getAttr(plug) or "") == sound_file:
            matches.append(node)
    return matches


def _validate_sources(actor: str, maya_node: str, wav_path: str | Path, txt_path: str | Path,
                      cmds_module: Any) -> tuple[str, Path, Path, str]:
    requested_rig = str(maya_node).strip()
    if not requested_rig or not cmds_module.objExists(requested_rig):
        raise RuntimeError(
            f"{actor}: mapped JALI_GRP does not exist: {requested_rig or '<empty>'}"
        )
    rig_matches = [str(item) for item in (cmds_module.ls(requested_rig, long=True) or [])]
    if len(rig_matches) != 1:
        raise RuntimeError(
            f"{actor}: mapped JALI_GRP must resolve to exactly one DAG node: "
            f"{requested_rig!r} -> {rig_matches}"
        )
    rig = rig_matches[0]
    wav, txt = Path(wav_path).resolve(), Path(txt_path).resolve()
    if not wav.is_file():
        raise FileNotFoundError(f"{actor}: WAV not found: {wav}")
    if not txt.is_file():
        raise FileNotFoundError(f"{actor}: TXT not found: {txt}")
    if wav.stem.casefold() != txt.stem.casefold():
        raise RuntimeError(f"{actor}: WAV/TXT stems do not match: {wav.name}, {txt.name}")
    return rig, wav, txt, wav.stem


_JALI_GLOBALS = (
    ("silence_handling", "int", "jalitest_get_silence_handling"),
    ("silence_handling_decibel", "float", "jalitest_get_silence_threshold"),
    ("jali_afscratch", "int", "jalitest_get_animate_from_scratch"),
)


def _read_mel_global(mel_module: Any, name: str, mel_type: str, getter: str) -> Any:
    if not mel_module.eval(f'exists "{getter}"'):
        mel_module.eval(
            f"global proc {mel_type} {getter}() {{ "
            f"global {mel_type} ${name}; return ${name}; }}"
        )
    return mel_module.eval(f"{getter}()")


def _set_mel_global(mel_module: Any, name: str, mel_type: str, value: Any) -> None:
    rendered = str(int(value)) if mel_type == "int" else repr(float(value))
    mel_module.eval(f"global {mel_type} ${name}; ${name} = {rendered};")


@contextmanager
def jali_alignment_settings(
    mel_module: Any,
    settings: dict[str, Any],
    *,
    animate_from_scratch: bool,
) -> Iterable[None]:
    """Temporarily apply the three JALI alignment globals used by call_jSync."""
    normalized = normalize_jali_speech_settings(settings)
    old = {
        name: _read_mel_global(mel_module, name, mel_type, getter)
        for name, mel_type, getter in _JALI_GLOBALS
    }
    desired = {
        "silence_handling": int(normalized["filter_silence_gaps"]),
        "silence_handling_decibel": normalized["silence_threshold_db"],
        "jali_afscratch": int(bool(animate_from_scratch)),
    }
    try:
        for name, mel_type, _getter in _JALI_GLOBALS:
            _set_mel_global(mel_module, name, mel_type, desired[name])
        yield
    finally:
        for name, mel_type, _getter in _JALI_GLOBALS:
            _set_mel_global(mel_module, name, mel_type, old[name])


def inspect_jali_speech_base(
    *, actor: str, script_name: str, maya_node: str, wav_path: str | Path,
    txt_path: str | Path, saved_metadata: dict[str, Any] | None = None,
    jali_settings: dict[str, Any] | None = None,
    cmds_module: Any | None = None,
) -> dict[str, Any]:
    """Inspect an exact rig+sound base and determine conservative reuse."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    rig, wav, txt, sound = _validate_sources(
        actor, maya_node, wav_path, txt_path, cmds_module
    )
    matches = _matching_nodes(cmds_module, rig, sound)
    if len(matches) > 1:
        raise RuntimeError(
            f"{actor}: ambiguous matching jSync nodes beneath {rig!r}: {', '.join(matches)}"
        )
    if not matches:
        return {"reusable": False, "reason": "no matching jSync", "sound_file": sound,
                "wav_path": str(wav), "txt_path": str(txt), "txt_sha256": transcript_sha256(txt),
                **_wav_identity(wav)}
    jsync = resolve_jsync_for_character(rig, sound, cmds_module=cmds_module)
    missing = [attr for attr in REQUIRED_JSYNC_ATTRS if not cmds_module.objExists(f"{jsync}.{attr}")]
    if missing:
        return {"reusable": False, "reason": f"jSync missing attributes: {', '.join(missing)}",
                "jsync": jsync, "sound_file": sound, "wav_path": str(wav), "txt_path": str(txt),
                "txt_sha256": transcript_sha256(txt), **_wav_identity(wav)}
    live_sound = str(cmds_module.getAttr(f"{jsync}.sound_file") or "").strip()
    live_txt = resolve_jali_source_transcript_path(
        str(cmds_module.getAttr(f"{jsync}.text_input_path") or ""), live_sound
    )
    digest = transcript_sha256(txt)
    settings = normalize_jali_speech_settings(jali_settings)
    if live_sound != sound or _normalized_path(live_txt) != _normalized_path(txt):
        return {"reusable": False, "reason": "live jSync source identity does not match",
                "jsync": jsync, "sound_file": sound, "wav_path": str(wav), "txt_path": str(txt),
                "txt_sha256": digest, **_wav_identity(wav)}
    live_transcript = str(cmds_module.getAttr(f"{jsync}.transcript") or "")
    if not live_transcript.strip():
        return {
            "reusable": False,
            "reason": "live jSync has no transcript content",
            "jsync": jsync,
            "sound_file": sound,
            "wav_path": str(wav),
            "txt_path": str(txt),
            "txt_sha256": digest,
            **_wav_identity(wav),
        }
    expected_transcript = txt.read_text(encoding="utf-8").replace("ing _ ", "ing_")
    expected_transcript = expected_transcript.replace("\n", "").replace("\r", "")
    if live_transcript.replace("\n", "").replace("\r", "") != expected_transcript:
        return {
            "reusable": False,
            "reason": "live jSync transcript content does not match the source TXT",
            "jsync": jsync,
            "sound_file": sound,
            "wav_path": str(wav),
            "txt_path": str(txt),
            "txt_sha256": digest,
            **_wav_identity(wav),
        }
    if str(cmds_module.getAttr(f"{jsync}.sound_file_format") or "").casefold() != wav.suffix.casefold():
        return {
            "reusable": False,
            "reason": "live jSync audio format does not match the expected WAV",
            "jsync": jsync,
            "sound_file": sound,
            "wav_path": str(wav),
            "txt_path": str(txt),
            "txt_sha256": digest,
            **_wav_identity(wav),
        }
    live_filter = bool(cmds_module.getAttr(f"{jsync}.silence_handling"))
    live_threshold = float(cmds_module.getAttr(f"{jsync}.silence_handling_decibel"))
    if live_filter != settings["filter_silence_gaps"] or (
        live_filter
        and not math.isclose(
            live_threshold, settings["silence_threshold_db"], abs_tol=1e-6
        )
    ):
        return {
            "reusable": False,
            "reason": (
                "live jSync silence settings do not match: "
                f"requested filter={settings['filter_silence_gaps']}, "
                f"threshold={settings['silence_threshold_db']:g}; "
                f"actual filter={live_filter}, threshold={live_threshold:g}"
            ),
            "jsync": jsync,
            "sound_file": sound,
            "wav_path": str(wav),
            "txt_path": str(txt),
            "txt_sha256": digest,
            "actual_jali_settings": {
                "filter_silence_gaps": live_filter,
                "silence_threshold_db": live_threshold,
            },
            **_wav_identity(wav),
        }
    saved = saved_metadata if isinstance(saved_metadata, dict) else None
    if saved:
        expected = {
            "script_name": str(script_name), "maya_node": rig, "jsync": jsync,
            "sound_file": sound, "wav_path": str(wav), "txt_path": str(txt), "txt_sha256": digest,
            **_wav_identity(wav),
        }
        mismatches = [key for key, value in expected.items() if str(saved.get(key) or "") != str(value)]
        if not _jali_speech_settings_match(saved.get("jali_settings"), settings):
            mismatches.append("jali_settings")
        if mismatches:
            return {**expected, "jali_settings": settings, "reusable": False,
                    "reason": "saved speech-base identity/fingerprint changed: " + ", ".join(mismatches)}
    return {
        "reusable": True, "reason": "exact live source identity and fingerprint match",
        "script_name": str(script_name), "maya_node": rig, "jsync": jsync,
        "sound_file": sound, "wav_path": str(wav), "txt_path": str(txt), "txt_sha256": digest,
        "jali_settings": settings,
        "actual_jali_settings": {
            "filter_silence_gaps": live_filter,
            "silence_threshold_db": live_threshold,
        },
        **_wav_identity(wav),
    }


def resolve_existing_jali_speech_base(
    *, actor: str, script_name: str, maya_node: str, wav_path: str | Path,
    cmds_module: Any | None = None,
) -> dict[str, Any]:
    """Resolve one existing/manual jSync without changing JALI or source files."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    requested_rig = str(maya_node).strip()
    if not requested_rig or not cmds_module.objExists(requested_rig):
        raise RuntimeError(f"{actor}: mapped JALI_GRP does not exist: {requested_rig or '<empty>'}")
    rig_matches = [str(item) for item in (cmds_module.ls(requested_rig, long=True) or [])]
    if len(rig_matches) != 1:
        raise RuntimeError(
            f"{actor}: mapped JALI_GRP must resolve to exactly one DAG node: "
            f"{requested_rig!r} -> {rig_matches}"
        )
    rig = rig_matches[0]
    wav = Path(wav_path).resolve()
    if not wav.is_file():
        raise FileNotFoundError(f"{actor}: WAV not found: {wav}")
    sound = wav.stem
    matches = _matching_nodes(cmds_module, rig, sound)
    if len(matches) != 1:
        rendered = ", ".join(matches) or "none"
        raise RuntimeError(
            f"{actor}: overlay-only Generate requires exactly one existing jSync for "
            f"{sound!r} beneath {rig!r}; found {len(matches)} ({rendered}). "
            "Run JALI Animate from File for this actor, then retry with "
            "Prepare JALI Speech unchecked."
        )
    jsync = matches[0]
    required = ("sound_file", "text_input_path", "transcript", "sound_file_format")
    missing = [attr for attr in required if not cmds_module.objExists(f"{jsync}.{attr}")]
    if missing:
        raise RuntimeError(
            f"{actor}: existing jSync {jsync!r} is missing: {', '.join(missing)}"
        )
    if not str(cmds_module.getAttr(f"{jsync}.transcript") or "").strip():
        raise RuntimeError(f"{actor}: existing jSync {jsync!r} has no aligned transcript.")
    if str(cmds_module.getAttr(f"{jsync}.sound_file_format") or "").casefold() != wav.suffix.casefold():
        raise RuntimeError(
            f"{actor}: existing jSync {jsync!r} does not use {wav.suffix} audio."
        )
    txt = resolve_jali_source_transcript_path(
        str(cmds_module.getAttr(f"{jsync}.text_input_path") or ""), sound
    )
    if not txt.is_file():
        raise FileNotFoundError(f"{actor}: existing jSync transcript not found: {txt}")
    return {
        "script_name": str(script_name),
        "maya_node": rig,
        "jsync": jsync,
        "sound_file": sound,
        "wav_path": str(wav),
        "txt_path": str(txt.resolve()),
        "transcript_path": str(txt.resolve()),
        "preparation_status": "existing",
    }


def _restore_selection(cmds_module: Any, selection: list[str]) -> None:
    if selection:
        cmds_module.select(selection, replace=True)
    else:
        cmds_module.select(clear=True)


def _jali_selection_for_rig(cmds_module: Any, rig: str) -> str:
    """Select FACSMaster so JALI resolves the intended namespace in multi-rig scenes."""
    try:
        descendants = cmds_module.listRelatives(
            rig, allDescendents=True, fullPath=True
        ) or []
    except (AttributeError, TypeError):
        descendants = []
    facs_masters = [
        str(node)
        for node in descendants
        if str(node).rsplit("|", 1)[-1].rsplit(":", 1)[-1] == "FACSMaster"
    ]
    if len(facs_masters) > 1:
        raise RuntimeError(
            f"Mapped JALI rig {rig!r} contains multiple FACSMaster controls: "
            + ", ".join(facs_masters)
        )
    return facs_masters[0] if facs_masters else rig


def _retire_alignment_cache(
    folder: Path, sound: str, digest: str
) -> list[tuple[Path, Path]]:
    """Preserve, but deactivate, JALI alignment files before a fresh run."""
    candidates = [folder / f"{sound}_PraatOutput.txt"]
    candidates.extend(sorted(folder.glob(f"{sound}*.TextGrid")))
    candidates.extend(sorted(folder.glob(f"{sound}*.Textgrid")))
    retired: list[tuple[Path, Path]] = []
    for source in dict.fromkeys(candidates):
        if not source.is_file():
            continue
        suffix = f".JALITEST_STALE_{digest[:12]}"
        target = source.with_name(source.name + suffix)
        counter = 1
        while target.exists():
            target = source.with_name(source.name + suffix + f".{counter}")
            counter += 1
        source.replace(target)
        retired.append((source, target))
    return retired


def _restore_alignment_cache(retired: Iterable[tuple[Path, Path]]) -> None:
    for source, backup in reversed(list(retired)):
        if backup.is_file():
            if source.is_file():
                source.unlink()
            backup.replace(source)


def _delete_nodes(cmds_module: Any, nodes: Iterable[str]) -> None:
    existing = [node for node in nodes if cmds_module.objExists(node)]
    if existing:
        cmds_module.delete(existing)


def prepare_jali_speech_base(
    *, actor: str, script_name: str, maya_node: str, wav_path: str | Path,
    txt_path: str | Path, language_code: int, speech_style: int,
    jali_settings: dict[str, Any] | None = None,
    animate_from_scratch: bool = False,
    known_mapped_rigs: Iterable[str] = (), cmds_module: Any | None = None,
    mel_module: Any | None = None,
) -> dict[str, Any]:
    """Run JALI's native prepare/analyze/create sequence for one actor rig."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    if mel_module is None:
        from maya import mel as mel_module  # type: ignore
    rig, wav, txt, sound = _validate_sources(
        actor, maya_node, wav_path, txt_path, cmds_module
    )
    settings = normalize_jali_speech_settings(jali_settings)
    ensure_jali_runtime_available(mel_module=mel_module)
    original = [str(item) for item in (cmds_module.ls(selection=True, long=True) or [])]
    rigs = [str(item) for item in known_mapped_rigs]
    before_all = set(_jsync_nodes(cmds_module))
    before = {rig: _nodes_under(cmds_module, rig) for rig in rigs}
    stale_matches = _matching_nodes(cmds_module, rig, sound)
    if len(stale_matches) > 1:
        raise RuntimeError(
            f"{actor}: ambiguous stale matching jSync nodes beneath {rig!r}: "
            + ", ".join(stale_matches)
        )
    retired_node: tuple[str, str] | None = None
    retired_cache: list[tuple[Path, Path]] = []
    try:
        if stale_matches:
            stale = stale_matches[0]
            retired_sound = f"{sound}__JALITEST_STALE_{transcript_sha256(txt)[:12]}"
            cmds_module.setAttr(f"{stale}.sound_file", retired_sound, type="string")
            retired_node = (stale, sound)
        retired_cache = _retire_alignment_cache(
            wav.parent, sound, transcript_sha256(txt)
        )
        cmds_module.select(_jali_selection_for_rig(cmds_module, rig), replace=True)
        folder = _mel_folder(wav.parent)
        prepare_command = (
            f'jali_prepare_folder_for_aligning("{folder}", "{folder}", '
            f'"{_mel_string(sound)}", 0, 1);'
        )
        analyze_command = (
            f'jAnalyze_batch("{folder}", "{folder}", '
            f'"{_mel_string(sound)}", {int(language_code)});'
        )
        create_command = (
            f'call_jSync("{_mel_folder(txt.parent)}", "{folder}", "{folder}", '
            f'"{_mel_string(sound)}", {int(language_code)}, {int(speech_style)});'
        )
        with jali_alignment_settings(
            mel_module, settings, animate_from_scratch=animate_from_scratch
        ):
            mel_module.eval(prepare_command)
            mel_module.eval(analyze_command)
            alignment_output = wav.parent / f"{sound}_PraatOutput.txt"
            if not alignment_output.is_file():
                raise RuntimeError(
                    f"{actor}: native JALI alignment did not create {alignment_output}"
                )
            # jAnalyze_batch changes Maya's active selection. Native Animate from
            # File restores the user's rig selection immediately before
            # call_jSync so jali_find_prefix resolves the correct character.
            cmds_module.select(_jali_selection_for_rig(cmds_module, rig), replace=True)
            mel_module.eval(create_command)
        matches = _matching_nodes(cmds_module, rig, sound)
        if len(matches) != 1:
            all_new = sorted(set(_jsync_nodes(cmds_module)) - before_all)
            discovered = {
                rig: sorted(_nodes_under(cmds_module, rig) - before.get(rig, set()))
                for rig in rigs
            }
            raise RuntimeError(
                f"{actor}: JALI did not create exactly one resolvable {sound!r} jSync beneath expected "
                f"rig {rig!r}. New jSync nodes: {all_new}. "
                f"Discovered nodes by mapped rig: {discovered}"
            )
        # The native Animate-from-File pipeline aligns before call_jSync. In that
        # case call_jSync reports "Already aligned" and leaves these two node
        # attributes at factory defaults even though jAnalyze_batch used the
        # requested globals. Persist the effective values on the resulting node
        # and immediately verify them through inspect_jali_speech_base below.
        created_jsync = matches[0]
        cmds_module.setAttr(
            f"{created_jsync}.silence_handling",
            int(settings["filter_silence_gaps"]),
        )
        cmds_module.setAttr(
            f"{created_jsync}.silence_handling_decibel",
            float(settings["silence_threshold_db"]),
        )
        inspected = inspect_jali_speech_base(
            actor=actor, script_name=script_name, maya_node=rig, wav_path=wav,
            txt_path=txt, saved_metadata=None, jali_settings=settings,
            cmds_module=cmds_module,
        )
        if not inspected["reusable"]:
            raise RuntimeError(f"{actor}: JALI postcondition failed: {inspected['reason']}")
        transcript_plug = f"{inspected['jsync']}.transcript"
        format_plug = f"{inspected['jsync']}.sound_file_format"
        if (
            not cmds_module.objExists(transcript_plug)
            or not str(cmds_module.getAttr(transcript_plug) or "").strip()
        ):
            raise RuntimeError(
                f"{actor}: JALI created {inspected['jsync']} without a transcript; "
                "native speech alignment did not complete."
            )
        if (
            not cmds_module.objExists(format_plug)
            or str(cmds_module.getAttr(format_plug) or "").casefold() != wav.suffix.casefold()
        ):
            raise RuntimeError(
                f"{actor}: JALI created {inspected['jsync']} without the expected "
                f"audio format {wav.suffix!r}; native speech alignment did not complete."
            )
        actual = inspected["actual_jali_settings"]
        if retired_node is not None:
            old_node, _old_sound = retired_node
            old_short_name = old_node.rsplit("|", 1)[-1]
            _delete_nodes(cmds_module, [old_node])
            try:
                cmds_module.rename(inspected["jsync"], old_short_name)
            except Exception:
                # A valid replacement need not fail solely because Maya retained
                # its generated node name.
                pass
            inspected["jsync"] = resolve_jsync_for_character(
                rig, sound, cmds_module=cmds_module
            )
    except Exception:
        _delete_nodes(cmds_module, sorted(set(_jsync_nodes(cmds_module)) - before_all))
        if retired_node is not None and cmds_module.objExists(retired_node[0]):
            cmds_module.setAttr(
                f"{retired_node[0]}.sound_file", retired_node[1], type="string"
            )
        _restore_alignment_cache(retired_cache)
        raise
    finally:
        _restore_selection(cmds_module, original)
    return {
        **{key: inspected[key] for key in ("script_name", "maya_node", "jsync", "sound_file", "wav_path", "txt_path", "txt_sha256", "wav_size", "wav_mtime_ns")},
        "jali_settings": settings,
        "actual_jali_settings": actual,
        "alignment_status": "prepared",
        "animated_from_scratch": bool(animate_from_scratch),
        "preparation_status": "prepared", "prepared_at": datetime.now(timezone.utc).isoformat(),
    }


def ensure_jali_speech_base(
    *, actor: str, script_name: str, maya_node: str, wav_path: str | Path,
    txt_path: str | Path, saved_metadata: dict[str, Any] | None,
    language_code: int, speech_style: int, known_mapped_rigs: Iterable[str],
    jali_settings: dict[str, Any] | None = None,
    force_from_scratch: bool = False,
    cmds_module: Any, mel_module: Any,
) -> dict[str, Any]:
    settings = normalize_jali_speech_settings(jali_settings)
    inspected = {"reusable": False, "reason": "forced from scratch"}
    # The live node exposes its transcript and effective silence settings, so a
    # matching native/manual jSync can be adopted even without a sidecar.
    if not force_from_scratch:
        inspected = inspect_jali_speech_base(
            actor=actor, script_name=script_name, maya_node=maya_node, wav_path=wav_path,
            txt_path=txt_path, saved_metadata=saved_metadata,
            jali_settings=settings, cmds_module=cmds_module,
        )
    if inspected["reusable"]:
        return {
            **{key: inspected[key] for key in ("script_name", "maya_node", "jsync", "sound_file", "wav_path", "txt_path", "txt_sha256", "wav_size", "wav_mtime_ns")},
            "jali_settings": settings,
            "actual_jali_settings": inspected["actual_jali_settings"],
            "alignment_status": "reused",
            "animated_from_scratch": False,
            "preparation_status": "reused",
            "prepared_at": str((saved_metadata or {}).get("prepared_at") or datetime.now(timezone.utc).isoformat()),
        }
    return prepare_jali_speech_base(
        actor=actor, script_name=script_name, maya_node=maya_node, wav_path=wav_path,
        txt_path=txt_path, language_code=language_code, speech_style=speech_style,
        jali_settings=settings, animate_from_scratch=force_from_scratch,
        known_mapped_rigs=known_mapped_rigs, cmds_module=cmds_module, mel_module=mel_module,
    )


def ensure_dual_jali_speech_bases(
    *, actors: Iterable[str], character_mappings: dict[str, dict[str, Any]],
    source_transcripts: dict[str, dict[str, Any]], saved_metadata: dict[str, Any] | None = None,
    jali_settings: dict[str, Any] | None = None, force_from_scratch: bool = False,
    config_path: str | Path = DEFAULT_MAYA_CONFIG, cmds_module: Any | None = None,
    mel_module: Any | None = None, status_callback: StatusCallback | None = None,
) -> dict[str, dict[str, Any]]:
    """Preflight both actors, then prepare/reuse both atomically before overlays."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    if mel_module is None:
        from maya import mel as mel_module  # type: ignore
    names = [str(actor) for actor in actors]
    if len(names) != 2 or names[0].casefold() == names[1].casefold():
        raise ValueError("Dual JALI speech preparation requires two distinct actors.")
    config = load_jali_speech_base_config(config_path)
    settings = normalize_jali_speech_settings(jali_settings or config)
    preflight: dict[str, dict[str, Any]] = {}
    for actor in names:
        mapping, source = character_mappings.get(actor), source_transcripts.get(actor)
        if not isinstance(mapping, dict) or not isinstance(source, dict):
            raise RuntimeError(f"{actor}: character mapping and source transcript are required.")
        script_name, rig = str(mapping.get("script_name") or ""), str(mapping.get("maya_node") or "")
        rig, wav, txt, sound = _validate_sources(
            actor, rig, source.get("wav") or "", source.get("txt") or "", cmds_module
        )
        if script_name.casefold() != actor.casefold():
            raise RuntimeError(f"{actor}: Script Character mapping does not match the Performance Plan.")
        preflight[actor] = {"script_name": script_name, "maya_node": rig, "wav": wav, "txt": txt, "sound": sound}
        if status_callback:
            status_callback(actor, sound, "will_prepare")
    ensure_jali_runtime_available(mel_module=mel_module)
    prepared: dict[str, dict[str, Any]] = {}
    rigs = [preflight[actor]["maya_node"] for actor in names]
    for index, actor in enumerate(names):
        item = preflight[actor]
        if status_callback:
            status_callback(actor, item["sound"], "preparing")
            for waiting in names[index + 1:]:
                status_callback(waiting, preflight[waiting]["sound"], "waiting")
        try:
            result = ensure_jali_speech_base(
                actor=actor, script_name=item["script_name"], maya_node=item["maya_node"],
                wav_path=item["wav"], txt_path=item["txt"],
                saved_metadata=(saved_metadata or {}).get(actor),
                language_code=config["language_code"], speech_style=config["speech_style"],
                jali_settings=settings, force_from_scratch=force_from_scratch,
                known_mapped_rigs=rigs, cmds_module=cmds_module, mel_module=mel_module,
            )
        except Exception:
            if status_callback:
                status_callback(actor, item["sound"], "failed")
                for not_started in names[index + 1:]:
                    status_callback(
                        not_started,
                        preflight[not_started]["sound"],
                        "not_started",
                    )
            raise
        prepared[actor] = result
        if status_callback:
            status_callback(actor, item["sound"], result["preparation_status"])
    return prepared
