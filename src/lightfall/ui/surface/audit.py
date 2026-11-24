"""Audit: which widgets can the agent UI surface not name or describe?

Findings are the to-do list for making Lightfall agent-consumable (and accessible):

- ``unnamed``: an interactive widget (button, field, list, ...) with no name. Fix with
  ``setAccessibleName``, a form-layout label or ``QLabel.setBuddy``.
- ``ambiguous``: two widgets with the same role and name in one panel or dialog, so an
  agent cannot tell them apart without a ref.
- ``undescribed``: a custom-painted widget (overrides ``paintEvent``, no interactive
  children) that does not implement ``AgentDescribable``, so an agent sees nothing of it.
"""

from __future__ import annotations

import inspect
from collections import Counter
from dataclasses import dataclass

from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QGraphicsView, QTabWidget, QWidget

from lightfall.ui.surface.roles import INTERACTIVE, is_describable, role_of
from lightfall.ui.surface.tree import Node, build_root
from lightfall.utils.crash_diagnostics import gui_thread_only


@dataclass
class Finding:
    kind: str
    panel: str
    role: str
    name: str
    widget_class: str
    object_name: str
    source: str  # module:line of the widget's class

    def row(self) -> str:
        return (f"| {self.kind} | {self.panel} | {self.role} | {self.name or '—'} | "
                f"`{self.widget_class}` | {self.object_name or '—'} | {self.source} |")


def _source(w: QWidget) -> str:
    """Where to fix it: the widget's own class, or for a plain Qt widget the nearest
    Lightfall-defined ancestor that created it."""
    cls = type(w)
    if cls.__module__.startswith("PySide6"):
        parent = w.parentWidget()
        while parent is not None and type(parent).__module__.startswith("PySide6"):
            parent = parent.parentWidget()
        return f"in {_class_source(parent)}" if parent is not None else cls.__module__
    return _class_source(w)


def _class_source(w: QWidget) -> str:
    cls = type(w)
    try:
        file = inspect.getsourcefile(cls) or ""
        line = inspect.getsourcelines(cls)[1]
    except (TypeError, OSError):
        return cls.__module__
    if "site-packages" in file or not file:
        return cls.__module__
    return f"{cls.__module__}:{line}"


def _custom_painted(w: QWidget) -> bool:
    cls = type(w)
    if cls.__module__.startswith(("PySide6", "shiboken")):
        return False
    if role_of(w) is not None or is_describable(w):
        return False
    if not isinstance(w, (QWidget, QFrame, QGraphicsView, QAbstractScrollArea)):
        return False
    overrides = any("paintEvent" in vars(k) for k in cls.__mro__
                    if not k.__module__.startswith(("PySide6", "shiboken", "builtins")))
    if not overrides and not isinstance(w, QGraphicsView):
        return False
    interactive_children = any(role_of(c) in INTERACTIVE for c in w.findChildren(QWidget))
    return not interactive_children and w.isVisible() and w.width() > 8 and w.height() > 8


def _findings_in(root: Node, top: QWidget) -> list[Finding]:
    out: list[Finding] = []
    seen: Counter = Counter()
    for n in root.walk():
        if n.widget is None or n.role == "item":
            continue
        panel = n.owner or root.name
        if n.role in INTERACTIVE and n.role != "menuitem" and not n.name:
            out.append(Finding("unnamed", panel, n.role, "", type(n.widget).__name__,
                               n.widget.objectName(), _source(n.widget)))
        if n.role in INTERACTIVE and n.name:
            seen[(panel, n.role, n.name.casefold())] += 1
    for (panel, role, name), count in seen.items():
        if count > 1:
            out.append(Finding("ambiguous", panel, role, f"{name} (×{count})", "", "", ""))
    for w in top.findChildren(QWidget):
        if _custom_painted(w):
            out.append(Finding("undescribed", root.name, "—", "", type(w).__name__,
                               w.objectName(), _source(w)))
    return out


@gui_thread_only
def audit_window(top: QWidget, process_events=None) -> list[Finding]:
    """Audit one window, visiting every page of every tab widget once."""
    findings = _findings_in(build_root(top), top)
    for tabs in top.findChildren(QTabWidget):
        if not tabs.isVisible():
            continue
        current = tabs.currentIndex()
        for i in range(tabs.count()):
            if i == current:
                continue
            tabs.setCurrentIndex(i)
            if process_events:
                process_events()
            findings.extend(f for f in _findings_in(build_root(top), top) if f not in findings)
        tabs.setCurrentIndex(current)
    # One row per distinct finding.
    unique: list[Finding] = []
    for f in findings:
        if f not in unique:
            unique.append(f)
    return unique


def report(results: dict[str, list[Finding]], failures: dict[str, str]) -> str:
    lines = ["# Agent UI surface audit", ""]
    total = Counter(f.kind for fs in results.values() for f in fs)
    lines.append(f"{sum(total.values())} findings: "
                 + ", ".join(f"{k} {v}" for k, v in sorted(total.items())) + "")
    lines.append("")
    for panel, findings in results.items():
        lines += [f"## {panel}", ""]
        if not findings:
            lines += ["No findings.", ""]
            continue
        lines += ["| kind | panel/dialog | role | name | class | objectName | source |",
                  "|---|---|---|---|---|---|---|"]
        lines += [f.row() for f in findings]
        lines.append("")
    if failures:
        lines += ["## Panels that could not be built offscreen", ""]
        lines += [f"- {k}: {v}" for k, v in failures.items()]
        lines.append("")
    return "\n".join(lines)
