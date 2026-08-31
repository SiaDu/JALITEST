"""Automatic native JALI speech-base preparation for dual authoring.

The module is intentionally independent of Qt and accepts injected Maya
``cmds``/``mel`` modules so its startup, inspection, reuse, and selection
safety can be tested outside Maya.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
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
)
STATUS_TEXT = {
    "will_prepare": "Will prepare automatically",
    "waiting": "Waiting...",
    "preparing": "Preparing JALI speech...",
    "reused": "Ready - Reused",
    "prepared": "Ready - Prepared",
    "failed": "Failed",
    "not_started": "Not started",
}
StatusCallback = Callable[[str, str, str], None]


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


def load_jali_speech_base_config(config_path: str | Path = DEFAULT_MAYA_CONFIG) -> dict[str, int]:
    # Maya's bundled Python does not include PyYAML. Reuse the repository's
    # established Maya-compatible loader instead of adding a runtime package.
    from expregaze_jali.maya_apply_gaze import _load_yaml_file
    raw = _load_yaml_file(Path(config_path))
    section = raw.get("maya_jali_speech_base") if isinstance(raw, dict) else None
    if not isinstance(section, dict):
        raise ValueError("Maya config requires a maya_jali_speech_base mapping.")
    result: dict[str, int] = {}
    for key in ("language_code", "speech_style"):
        value = section.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"maya_jali_speech_base.{key} must be a non-negative integer.")
        result[key] = value
    return result


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
    """Load JALI's documented startup script when ``call_jSync`` is absent."""
    if mel_module is None:
        from maya import mel as mel_module  # type: ignore
    if mel_module.eval('exists "call_jSync"'):
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
    if not mel_module.eval('exists "call_jSync"'):
        raise RuntimeError(
            "Automatic JALI preparation is unavailable because call_jSync is not loaded. "
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


def inspect_jali_speech_base(
    *, actor: str, script_name: str, maya_node: str, wav_path: str | Path,
    txt_path: str | Path, saved_metadata: dict[str, Any] | None = None,
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
    if live_sound != sound or _normalized_path(live_txt) != _normalized_path(txt):
        return {"reusable": False, "reason": "live jSync source identity does not match",
                "jsync": jsync, "sound_file": sound, "wav_path": str(wav), "txt_path": str(txt),
                "txt_sha256": digest, **_wav_identity(wav)}
    if not str(cmds_module.getAttr(f"{jsync}.transcript") or "").strip():
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
    saved = saved_metadata if isinstance(saved_metadata, dict) else None
    if saved:
        expected = {
            "script_name": str(script_name), "maya_node": rig, "jsync": jsync,
            "sound_file": sound, "wav_path": str(wav), "txt_path": str(txt), "txt_sha256": digest,
            **_wav_identity(wav),
        }
        mismatches = [key for key, value in expected.items() if str(saved.get(key) or "") != str(value)]
        if mismatches:
            return {**expected, "reusable": False,
                    "reason": "saved speech-base identity/fingerprint changed: " + ", ".join(mismatches)}
    return {
        "reusable": True, "reason": "exact live source identity and fingerprint match",
        "script_name": str(script_name), "maya_node": rig, "jsync": jsync,
        "sound_file": sound, "wav_path": str(wav), "txt_path": str(txt), "txt_sha256": digest,
        **_wav_identity(wav),
    }


def _restore_selection(cmds_module: Any, selection: list[str]) -> None:
    if selection:
        cmds_module.select(selection, replace=True)
    else:
        cmds_module.select(clear=True)


def _retire_alignment_cache(folder: Path, sound: str, digest: str) -> list[Path]:
    """Preserve, but deactivate, JALI alignment files before a fresh run."""
    candidates = [folder / f"{sound}_PraatOutput.txt"]
    candidates.extend(sorted(folder.glob(f"{sound}*.TextGrid")))
    candidates.extend(sorted(folder.glob(f"{sound}*.Textgrid")))
    retired: list[Path] = []
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
        retired.append(target)
    return retired


def prepare_jali_speech_base(
    *, actor: str, script_name: str, maya_node: str, wav_path: str | Path,
    txt_path: str | Path, language_code: int, speech_style: int,
    known_mapped_rigs: Iterable[str] = (), cmds_module: Any | None = None,
    mel_module: Any | None = None,
) -> dict[str, Any]:
    """Invoke ``call_jSync`` once and prove the result belongs to the actor rig."""
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    if mel_module is None:
        from maya import mel as mel_module  # type: ignore
    rig, wav, txt, sound = _validate_sources(
        actor, maya_node, wav_path, txt_path, cmds_module
    )
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
    retired: tuple[str, str] | None = None
    try:
        if stale_matches:
            stale = stale_matches[0]
            retired_sound = f"{sound}__JALITEST_STALE_{transcript_sha256(txt)[:12]}"
            cmds_module.setAttr(f"{stale}.sound_file", retired_sound, type="string")
            retired = (stale, sound)
        _retire_alignment_cache(wav.parent, sound, transcript_sha256(txt))
        cmds_module.select(rig, replace=True)
        command = (
            f'call_jSync("{_mel_folder(txt.parent)}", "{_mel_folder(wav.parent)}", '
            f'"{_mel_folder(wav.parent)}", "{_mel_string(sound)}", '
            f"{int(language_code)}, {int(speech_style)});"
        )
        mel_module.eval(command)
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
        inspected = inspect_jali_speech_base(
            actor=actor, script_name=script_name, maya_node=rig, wav_path=wav,
            txt_path=txt, saved_metadata=None, cmds_module=cmds_module,
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
        alignment_output = wav.parent / f"{sound}_PraatOutput.txt"
        if not alignment_output.is_file():
            raise RuntimeError(
                f"{actor}: JALI alignment output was not created: {alignment_output}"
            )
    except Exception:
        for index, node in enumerate(
            sorted(set(_jsync_nodes(cmds_module)) - before_all), start=1
        ):
            plug = f"{node}.sound_file"
            if (
                cmds_module.objExists(plug)
                and str(cmds_module.getAttr(plug) or "") == sound
            ):
                cmds_module.setAttr(
                    plug,
                    f"{sound}__JALITEST_FAILED_{transcript_sha256(txt)[:12]}_{index}",
                    type="string",
                )
        if retired is not None:
            cmds_module.setAttr(f"{retired[0]}.sound_file", retired[1], type="string")
        raise
    finally:
        _restore_selection(cmds_module, original)
    return {
        **{key: inspected[key] for key in ("script_name", "maya_node", "jsync", "sound_file", "wav_path", "txt_path", "txt_sha256", "wav_size", "wav_mtime_ns")},
        "preparation_status": "prepared", "prepared_at": datetime.now(timezone.utc).isoformat(),
    }


def ensure_jali_speech_base(
    *, actor: str, script_name: str, maya_node: str, wav_path: str | Path,
    txt_path: str | Path, saved_metadata: dict[str, Any] | None,
    language_code: int, speech_style: int, known_mapped_rigs: Iterable[str],
    cmds_module: Any, mel_module: Any,
) -> dict[str, Any]:
    inspected = inspect_jali_speech_base(
        actor=actor, script_name=script_name, maya_node=maya_node, wav_path=wav_path,
        txt_path=txt_path, saved_metadata=saved_metadata, cmds_module=cmds_module,
    )
    if inspected["reusable"]:
        return {
            **{key: inspected[key] for key in ("script_name", "maya_node", "jsync", "sound_file", "wav_path", "txt_path", "txt_sha256", "wav_size", "wav_mtime_ns")},
            "preparation_status": "reused",
            "prepared_at": str((saved_metadata or {}).get("prepared_at") or datetime.now(timezone.utc).isoformat()),
        }
    return prepare_jali_speech_base(
        actor=actor, script_name=script_name, maya_node=maya_node, wav_path=wav_path,
        txt_path=txt_path, language_code=language_code, speech_style=speech_style,
        known_mapped_rigs=known_mapped_rigs, cmds_module=cmds_module, mel_module=mel_module,
    )


def ensure_dual_jali_speech_bases(
    *, actors: Iterable[str], character_mappings: dict[str, dict[str, Any]],
    source_transcripts: dict[str, dict[str, Any]], saved_metadata: dict[str, Any] | None = None,
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
