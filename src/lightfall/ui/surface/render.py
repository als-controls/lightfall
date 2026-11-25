"""Render node trees as the compact text outline the agent reads."""

from __future__ import annotations

import json

from lightfall.ui.surface.tree import Node, interactive_count

MAX_INFO = 300


def _info_text(node: Node) -> str:
    info = {k: v for k, v in node.info.items() if k not in ("opaque", "depth")}
    parts = []
    if node.role in ("list", "table", "tree"):
        rows = info.pop("rows", 0)
        parts.append(f"{rows} item{'s' if rows != 1 else ''}")
        if "showing" in info:
            parts.append(f"showing {info.pop('showing')}")
    if node.role == "combo" and "options" in info:
        parts.append(f"{info.pop('options')} options")
    if "collapsed_controls" in info:
        parts.append(f"collapsed, {info.pop('collapsed_controls')} controls; "
                     f"ui_snapshot scope={node.ref} to expand")
    if info:
        text = json.dumps(info, separators=(", ", ": "), default=str)
        parts.append(text if len(text) <= MAX_INFO else text[:MAX_INFO] + "…}")
    return ", ".join(parts)


def line(node: Node, depth: int) -> str:
    out = "  " * depth + node.role
    if node.name:
        out += f' "{node.name}"'
    else:
        out += f" (unnamed{', objectName=' + node.hint if node.hint else ''})"
    if node.ref:
        out += f" [{node.ref}]"
    if node.value is not None:
        out += f' = "{node.value}"'
    if node.state:
        out += " " + " ".join(node.state)
    info = _info_text(node)
    if info:
        out += " " + info
    return out


def render(nodes: list[Node], max_lines: int = 400) -> str:
    lines: list[str] = []
    total = 0

    def emit(n: Node, depth: int) -> None:
        nonlocal total
        total += 1
        if len(lines) < max_lines:
            item_depth = n.info.get("depth", 0) if n.role == "item" else 0
            lines.append(line(n, depth + item_depth))
        for c in n.children:
            emit(c, depth + 1)

    for n in nodes:
        emit(n, 0)
    if total > len(lines):
        lines.append(f"… {total - len(lines)} more lines; use ui_snapshot with scope=<ref or "
                     f"panel name> to see one part in full")
    return "\n".join(lines)


def collapse(node: Node) -> None:
    """Replace a node's children by a count (summary mode)."""
    node.info["collapsed_controls"] = interactive_count(node)
    node.children = []
