"""Read-only Maya probe for evidencing JALI 2025 expressive-eyelid mappings."""
from __future__ import annotations

from typing import Any

_JOINTS = ("L_LidJoint_Up", "R_LidJoint_Up", "L_LidJoint_Lo", "R_LidJoint_Lo")
_TOKENS = ("usr_", "jal_", "facsmaster", "blendweighted", "plusminusaverage", "unitconversion", "lid", "squint", "blink")


def _namespace(root: str) -> str:
    leaf = str(root).rsplit("|", 1)[-1]
    return leaf.rsplit(":", 1)[0] if ":" in leaf else ""


def diagnose_eyelid_user_mappings(
    rig_root: str, *, cmds_module: Any | None = None, max_depth: int = 6
) -> dict[str, list[tuple[str, str]]]:
    """Trace only eyelid-joint rotateX upstream graph edges without scene mutation.

    The result deliberately reports exact Maya plug pairs rather than assigning
    AU05/AU07/AU41.  A live rig inspection must establish that correspondence.
    """
    if cmds_module is None:
        from maya import cmds as cmds_module  # type: ignore
    namespace = _namespace(rig_root)
    report: dict[str, list[tuple[str, str]]] = {}
    for joint in _JOINTS:
        node = f"{namespace}:{joint}" if namespace else joint
        plug = f"{node}.rotateX"
        if not cmds_module.objExists(plug):
            report[plug] = []
            continue
        edges: list[tuple[str, str]] = []
        pending = [(plug, 0)]
        seen = {plug}
        while pending:
            destination, depth = pending.pop(0)
            raw = list(cmds_module.listConnections(
                destination, source=True, destination=False, plugs=True, connections=True
            ) or [])
            for index in range(0, len(raw) - 1, 2):
                left, right = str(raw[index]), str(raw[index + 1])
                source = right if left == destination else left
                edge = (source, destination)
                if edge not in edges:
                    edges.append(edge)
                if depth < max_depth and source not in seen and any(token in source.casefold() for token in _TOKENS):
                    seen.add(source)
                    pending.append((source, depth + 1))
        report[plug] = edges
    for plug, edges in report.items():
        print(f"[JALITEST eyelid probe] {plug}")
        for source, destination in edges:
            print(f"  {source} -> {destination}")
    return report
