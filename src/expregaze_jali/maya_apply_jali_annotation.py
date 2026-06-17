from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


def _cmds():
    try:
        import maya.cmds as cmds  # type: ignore
        return cmds
    except Exception as exc:
        raise RuntimeError(
            "maya_apply_jali_annotation must be run inside Autodesk Maya's Python environment."
        ) from exc


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        items = [item.strip() for item in text[1:-1].split(",") if item.strip()]
        return [_parse_scalar(item) for item in items]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    try:
        if any(char in text for char in (".", "e", "E")):
            return float(text)
        return int(text)
    except ValueError:
        return text.strip("\"'")


def _simple_yaml_load(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            raise ValueError(f"Unsupported YAML line: {raw_line!r}")

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if value:
            parent[key] = _parse_scalar(value)
        else:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))

    return root


def _load_yaml_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
    except ModuleNotFoundError:
        data = _simple_yaml_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def load_jali_annotation_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    data = _load_yaml_file(config_path)
    common = data.get("maya_common", {}) if isinstance(data, dict) else {}
    config = data.get("maya_jali_annotation", data)
    if not isinstance(common, dict) or not isinstance(config, dict):
        raise ValueError(f"Invalid JALI annotation config: {path}")

    out = {**common, **config}
    clip_name = str(out.get("clip_name", "")).strip()
    if "annotated_for_jali_path" not in out and clip_name:
        out["annotated_for_jali_path"] = f"data/processed/gaze_script/{clip_name}__annotated_for_jali.txt"
    if "jali_transcript_path" not in out and clip_name:
        input_dir = str(out.get("jali_input_dir", "")).strip()
        out["jali_transcript_path"] = f"{input_dir}/{clip_name}.txt" if input_dir else f"{clip_name}.txt"

    out["_config_path"] = str(config_path)

    repo_root = out.get("repo_root", ".")
    repo_root_path = Path(str(repo_root))
    inferred_project_root = config_path.parent.parent.parent
    if repo_root_path.is_absolute():
        project_root = repo_root_path
    else:
        project_root = inferred_project_root / repo_root_path

    out["_project_root"] = str(project_root.resolve() if project_root.exists() else project_root)
    return out


def resolve_repo_path(path_value: str | Path, config: dict[str, Any]) -> str:
    path = Path(path_value)
    if path.is_absolute():
        return str(path)
    return str(Path(config.get("_project_root", ".")) / path)


def resolve_maya_project_path(path_value: str | Path, config: dict[str, Any]) -> str:
    path = Path(path_value)
    if path.is_absolute():
        return str(path)
    project_root_value = config.get("maya_project_root")
    if not project_root_value:
        raise KeyError("maya_project_root is required to resolve Maya project paths.")
    return str(Path(str(project_root_value)) / path)


def _find_jsync_node(node_name: str) -> str:
    cmds = _cmds()
    if cmds.objExists(node_name):
        return node_name

    matches = cmds.ls(node_name, long=True) or cmds.ls(f"*:{node_name}", long=True) or []
    if not matches:
        raise RuntimeError(f"jSync node not found: {node_name}")
    return matches[0]


def _backup_file(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak_{stamp}")
    shutil.copy2(path, backup)
    return backup


def _set_attr_if_exists(node: str, attr: str, value: Any) -> None:
    cmds = _cmds()
    plug = f"{node}.{attr}"
    if not cmds.objExists(plug):
        print(f"[WARN] jSync attr missing, skipped: {plug}")
        return

    try:
        cmds.setAttr(plug, value)
    except TypeError:
        cmds.setAttr(plug, value, type="string")

    print(f"[INFO] Set {plug} = {value!r}")


def _try_set_transcript_on_jsync(node: str, annotated_text: str) -> None:
    """
    Best-effort sync to existing jSync node.

    JALI versions differ in transcript/text attr names, so this does not fail if
    none of the candidate attrs exist. The file replacement remains the source of truth.
    """
    cmds = _cmds()

    candidate_text_attrs = [
        "transcript",
        "text",
        "input_text",
        "override_text",
        "transcript_text",
    ]

    for attr in candidate_text_attrs:
        plug = f"{node}.{attr}"
        if cmds.objExists(plug):
            try:
                cmds.setAttr(plug, annotated_text, type="string")
                print(f"[INFO] Updated jSync transcript attr: {plug}")
                return
            except Exception as exc:
                print(f"[WARN] Failed to update {plug}: {exc}")

    print("[WARN] No writable jSync transcript/text attr found. File replacement still completed.")


def _trigger_jsync_compute(node: str) -> None:
    """
    Force a jSync evaluation if call_compute exists.

    This mirrors the common JALI scripting trick of connecting jSync.call_compute
    to a temporary node, forcing Maya/JALI to evaluate, then deleting the temp node.
    """
    cmds = _cmds()
    plug = f"{node}.call_compute"

    if not cmds.objExists(plug):
        print(f"[WARN] {plug} does not exist; cannot trigger jSync compute automatically.")
        return

    temp = cmds.createNode("transform", name="EXPGAZE_JALI_COMPUTE_TRIGGER_TMP")
    target = f"{temp}.translateX"

    try:
        cmds.connectAttr(plug, target, force=True)
        cmds.dgdirty(node)
        cmds.refresh(force=True)
        print(f"[INFO] Triggered jSync compute through {plug}")
    finally:
        try:
            if cmds.isConnected(plug, target):
                cmds.disconnectAttr(plug, target)
        except Exception:
            pass
        try:
            cmds.delete(temp)
        except Exception:
            pass


def apply_jali_annotation(
    annotated_for_jali_path: str | Path,
    jali_transcript_path: str | Path,
    jsync_node: str = "jSync1",
    backup_original_transcript: bool = True,
    trigger_jsync_compute: bool = True,
    jali_attribute_overrides: dict[str, Any] | None = None,
) -> None:
    annotated_path = Path(annotated_for_jali_path)
    transcript_path = Path(jali_transcript_path)

    if not annotated_path.exists():
        raise FileNotFoundError(f"annotated_for_jali not found: {annotated_path}")

    if not transcript_path.exists():
        raise FileNotFoundError(f"JALI transcript txt not found: {transcript_path}")

    annotated_text = annotated_path.read_text(encoding="utf-8")

    if backup_original_transcript:
        backup = _backup_file(transcript_path)
        print(f"[INFO] Backed up original transcript: {backup}")

    transcript_path.write_text(annotated_text, encoding="utf-8")
    print(f"[INFO] Wrote annotated JALI transcript:")
    print(f"       {transcript_path}")

    node = _find_jsync_node(jsync_node)

    for attr, value in (jali_attribute_overrides or {}).items():
        _set_attr_if_exists(node, str(attr), value)

    _try_set_transcript_on_jsync(node, annotated_text)

    if trigger_jsync_compute:
        _trigger_jsync_compute(node)

    print("[DONE] JALI annotation applied.")


def apply_jali_annotation_from_config(config_path: str | Path) -> None:
    config = load_jali_annotation_config(config_path)

    annotated_path = resolve_repo_path(config["annotated_for_jali_path"], config)
    transcript_path = resolve_maya_project_path(config["jali_transcript_path"], config)

    apply_jali_annotation(
        annotated_for_jali_path=annotated_path,
        jali_transcript_path=transcript_path,
        jsync_node=str(config.get("jsync_node", "jSync1")),
        backup_original_transcript=bool(config.get("backup_original_transcript", True)),
        trigger_jsync_compute=bool(config.get("trigger_jsync_compute", True)),
        jali_attribute_overrides=config.get("jali_attribute_overrides", {}),
    )