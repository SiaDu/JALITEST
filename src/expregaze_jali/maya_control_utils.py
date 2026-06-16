from __future__ import annotations

from typing import Sequence


def _cmds():
    try:
        import maya.cmds as cmds  # type: ignore
        return cmds
    except Exception as exc:
        raise RuntimeError(
            "maya_control_utils must be run inside Autodesk Maya's Python environment."
        ) from exc


def find_node_by_suffix(suffix: str) -> str:
    """
    Namespace-safe node lookup.

    Example:
        suffix='eyeStare_world'
        can match:
            eyeStare_world
            ValleyGirl:eyeStare_world
            some|group|ValleyGirl:eyeStare_world
    """
    cmds = _cmds()

    exact = cmds.ls(suffix, long=True) or []
    namespaced = cmds.ls(f"*:{suffix}", long=True) or []
    wildcard = cmds.ls(f"*{suffix}", long=True) or []

    matches = []
    seen = set()

    for node in exact + namespaced + wildcard:
        short = node.split("|")[-1]
        if short == suffix or short.endswith(f":{suffix}"):
            if node not in seen:
                matches.append(node)
                seen.add(node)

    if not matches:
        raise RuntimeError(f"Could not find Maya node ending with: {suffix}")

    if len(matches) > 1:
        print(f"[WARN] Multiple nodes match suffix {suffix!r}; using first:")
        for item in matches:
            print(f"  - {item}")

    return matches[0]


def find_eye_stare_world() -> str:
    return find_node_by_suffix("eyeStare_world")


def find_both_eyes_ctrl() -> str:
    return find_node_by_suffix("CNT_BOTH_EYES")


def sec_to_frame(seconds: float, fps: float) -> int:
    return int(round(float(seconds) * float(fps)))


def get_world_translation(node: str) -> list[float]:
    cmds = _cmds()
    return list(cmds.xform(node, query=True, worldSpace=True, translation=True))


def set_world_translation(node: str, xyz: Sequence[float]) -> None:
    cmds = _cmds()
    cmds.xform(node, worldSpace=True, translation=list(xyz))


def key_translate(node: str, frame: int) -> None:
    cmds = _cmds()
    for attr in ("translateX", "translateY", "translateZ"):
        cmds.setKeyframe(node, attribute=attr, time=frame)