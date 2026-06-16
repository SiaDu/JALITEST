from __future__ import annotations


def _cmds():
    try:
        import maya.cmds as cmds  # type: ignore
        return cmds
    except Exception as exc:
        raise RuntimeError(
            "print_jali_controls must be run inside Autodesk Maya's Python environment."
        ) from exc


KEYWORDS = (
    "blink",
    "gaze",
    "mask",
    "heart",
    "emotion",
    "intensity",
)


def _unique(items):
    out = []
    seen = set()
    for item in items:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _find_by_suffix(suffix: str):
    cmds = _cmds()
    matches = []
    for pattern in (suffix, f"*:{suffix}", f"*{suffix}"):
        matches.extend(cmds.ls(pattern, long=True) or [])

    filtered = []
    for node in matches:
        short = node.split("|")[-1]
        if short == suffix or short.endswith(f":{suffix}"):
            filtered.append(node)

    return _unique(filtered)


def _print_nodes(title: str, nodes: list[str]) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

    if not nodes:
        print("(none)")
        return

    for node in nodes:
        print(node)


def _safe_get_attr(node: str, attr: str):
    cmds = _cmds()
    plug = f"{node}.{attr}"
    try:
        if cmds.getAttr(plug, settable=True) is None:
            pass
        value = cmds.getAttr(plug)
        return repr(value)
    except Exception:
        return "<unreadable>"


def print_jali_controls() -> None:
    cmds = _cmds()

    all_nodes = cmds.ls(long=True) or []

    jsync_nodes = [
        node for node in all_nodes
        if "jsync" in node.split("|")[-1].lower()
    ]

    eye_stare_nodes = _find_by_suffix("eyeStare_world")
    both_eyes_nodes = _find_by_suffix("CNT_BOTH_EYES")

    _print_nodes("Nodes containing 'jSync'", jsync_nodes)
    _print_nodes("Nodes ending with 'eyeStare_world'", eye_stare_nodes)
    _print_nodes("Nodes ending with 'CNT_BOTH_EYES'", both_eyes_nodes)

    target_jsync = None
    if cmds.objExists("jSync1"):
        target_jsync = "jSync1"
    elif jsync_nodes:
        target_jsync = jsync_nodes[0]

    print("\n" + "=" * 80)
    print("jSync attributes matching blink/gaze/mask/heart/emotion/intensity")
    print("=" * 80)

    if not target_jsync:
        print("(no jSync node found)")
        return

    print(f"Using jSync node: {target_jsync}")

    attrs = cmds.listAttr(target_jsync) or []
    matched = [
        attr for attr in attrs
        if any(keyword in attr.lower() for keyword in KEYWORDS)
    ]

    if not matched:
        print("(no matching attributes)")
        return

    for attr in sorted(matched, key=str.lower):
        value = _safe_get_attr(target_jsync, attr)
        print(f"{target_jsync}.{attr} = {value}")


print_jali_controls()