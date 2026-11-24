"""ui_snapshot: what a person sees in Lightfall's windows, by role and name."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from lightfall.ui.surface.render import collapse, render
from lightfall.ui.surface.tree import REFS, Node, SurfaceError, build_all
from lightfall.utils.crash_diagnostics import gui_thread_only

#: Name of the dock/dialog the last ui_act happened in (kept expanded in summary mode).
_last_owner: str = ""

_SCOPE_ROLES = ("dock", "dialog", "window", "group", "tabs", "tab", "menu", "toast")


def set_last_owner(owner: str) -> None:
    global _last_owner
    _last_owner = owner or _last_owner


def _focus_owner() -> str:
    focus = QApplication.focusWidget()
    while focus is not None:
        from PySide6.QtWidgets import QDockWidget

        if isinstance(focus, QDockWidget):
            return focus.windowTitle()
        focus = focus.parentWidget()
    return ""


def find_scope(nodes: list[Node], scope: str) -> Node:
    """A node by ref (from the previous snapshot) or by dock/dialog/group name."""
    if scope and scope[0] in "pdwte" and scope[1:].isdigit():
        target = REFS.resolve(scope).widget
        for root in nodes:
            for n in root.walk():
                if n.widget is target and n.index is None and n.tab is None:
                    return n
        raise SurfaceError(f"'{scope}' is no longer on screen; take a new ui_snapshot")
    wanted = scope.casefold()
    matches = [n for root in nodes for n in root.walk()
               if n.role in _SCOPE_ROLES and n.name.casefold() == wanted]
    if not matches:
        matches = [n for root in nodes for n in root.walk()
                   if n.role in _SCOPE_ROLES and n.name.casefold().startswith(wanted)]
    if len(matches) == 1:
        return matches[0]
    names = sorted({f'{n.role} "{n.name}"' for root in nodes for n in root.walk()
                    if n.role in ("dock", "dialog", "window") and n.name})
    if not matches:
        raise SurfaceError(f"no panel or dialog named '{scope}'. Open: {', '.join(names)}")
    raise SurfaceError(f"'{scope}' is ambiguous: {', '.join(m.label for m in matches)}")


@gui_thread_only
def snapshot(scope: str | None = None, max_lines: int = 400) -> str:
    """The outline of visible windows (or of one scope), with fresh refs."""
    nodes = build_all()
    if scope:
        nodes = [find_scope(nodes, scope)]
    else:
        keep = {_focus_owner(), _last_owner} - {""}
        for root in nodes:
            if root.role != "window":
                continue
            docks = [n for n in root.walk() if n.role == "dock"]
            for dock in docks:
                if dock.name not in keep and len(docks) > 1:
                    collapse(dock)
    REFS.assign(nodes)
    return render(nodes, max_lines=max_lines)
