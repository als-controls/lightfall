"""Build the agent's view of the UI: a tree of role/name nodes with refs.

Everything here runs on the GUI thread. The tree is rebuilt for every snapshot and every
locate; refs handed out by one snapshot go stale at the next.
"""

from __future__ import annotations

import weakref
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import shiboken6
from PySide6.QtCore import QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFocusFrame,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRubberBand,
    QScrollBar,
    QSizeGrip,
    QSplitterHandle,
    QTabBar,
    QTableView,
    QTabWidget,
    QTextEdit,
    QTreeView,
    QWidget,
)

from lightfall.ui.surface.names import accessible_name, clean, labelled_by
from lightfall.ui.surface.roles import INTERACTIVE, LEAVES, is_describable, role_of

#: Item rows shown per list/table/tree.
MAX_ITEMS = 12
#: Tree levels shown below the top level.
MAX_TREE_DEPTH = 2
#: Columns of a table or multi-column tree shown per item (name column included).
MAX_COLUMNS = 4
MAX_TEXT = 200

_SKIPPED = (QScrollBar, QSizeGrip, QSplitterHandle, QRubberBand, QFocusFrame)


class SurfaceError(Exception):
    """A request the surface cannot satisfy; the message is written for the agent."""


class StaleRef(SurfaceError):
    pass


@dataclass(eq=False)
class Node:
    role: str
    name: str
    value: str | None = None
    state: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)
    #: objectName, shown only when the node has no name.
    hint: str = ""
    ref: str = ""
    #: Name of the nearest dock / dialog / window above this node ("" at a root).
    owner: str = ""
    widget: QWidget | None = field(default=None, repr=False)
    index: QPersistentModelIndex | None = field(default=None, repr=False)
    tab: int | None = None
    #: Menu actions are addressed through their menu widget plus the action.
    action: Any = field(default=None, repr=False)

    def walk(self) -> Iterator[Node]:
        yield self
        for c in self.children:
            yield from c.walk()

    @property
    def label(self) -> str:
        """Human label for messages: role "name" in owner."""
        base = f'{self.role} "{self.name}"' if self.name else f"{self.role} (unnamed)"
        return f'{base} in "{self.owner}"' if self.owner and self.owner != self.name else base


# --- refs -------------------------------------------------------------------------------

_PREFIXES = {"dock": "p", "dialog": "d", "window": "w", "toast": "t"}


class RefTable:
    """Refs handed out by the latest snapshot."""

    def __init__(self) -> None:
        self.generation = 0
        self._refs: dict[str, Node] = {}
        self._widgets: dict[str, weakref.ref] = {}

    def assign(self, nodes: list[Node]) -> None:
        self.generation += 1
        self._refs.clear()
        self._widgets.clear()
        counters: dict[str, int] = {}
        for root in nodes:
            for n in root.walk():
                prefix = _PREFIXES.get(n.role, "e")
                counters[prefix] = counters.get(prefix, 0) + 1
                n.ref = f"{prefix}{counters[prefix]}"
                if n.widget is not None:
                    self._refs[n.ref] = n
                    self._widgets[n.ref] = weakref.ref(n.widget)

    def resolve(self, ref: str) -> Node:
        node = self._refs.get(ref)
        if node is None:
            raise StaleRef(f"unknown or stale ref '{ref}'; take a new ui_snapshot")
        w = self._widgets[ref]()
        if (w is None or not shiboken6.isValid(w)
                or (node.index is not None and not node.index.isValid())):
            raise StaleRef(f"ref '{ref}' no longer exists; take a new ui_snapshot")
        return node


REFS = RefTable()


# --- helpers ----------------------------------------------------------------------------

def is_toast(w: QWidget) -> bool:
    try:
        from pyqttoast import Toast
    except ImportError:  # pragma: no cover - pyqttoast is a dependency
        return False
    return isinstance(w, Toast)


def _visible(w: QWidget) -> bool:
    return shiboken6.isValid(w) and w.isVisible()


def roots() -> list[QWidget]:
    """Visible top-level windows: main windows, then dialogs, menus, toasts, the rest."""

    def order(w: QWidget) -> int:
        if is_toast(w):
            return 3
        role = role_of(w)
        return {"window": 0, "dialog": 1, "menu": 2}.get(role or "", 4)

    tops = [w for w in QApplication.topLevelWidgets()
            if _visible(w) and type(w).__name__ != "QTipLabel"]
    return sorted(tops, key=order)


def _text_value(w: QWidget) -> str | None:
    if isinstance(w, QLineEdit):
        if w.echoMode() != QLineEdit.EchoMode.Normal:
            return "*" * len(w.text())
        return w.text()
    if isinstance(w, QAbstractSpinBox):
        return w.text()
    if isinstance(w, (QPlainTextEdit, QTextEdit)):
        return w.toPlainText()[:MAX_TEXT]
    if isinstance(w, QComboBox):
        return w.currentText()
    if isinstance(w, QProgressBar):
        return f"{w.value()}/{w.maximum()}"
    if isinstance(w, QAbstractSlider):
        return str(w.value())
    return None


def _state(w: QWidget, role: str) -> list[str]:
    s = []
    if not w.isEnabled():
        s.append("disabled")
    if role in ("checkbox", "radio", "toggle") and w.isChecked():
        s.append("checked")
    if isinstance(w, QPushButton) and w.isDefault():
        s.append("default")
    if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)) and w.isReadOnly():
        s.append("read-only")
    if isinstance(w, QDialog) and w.isModal():
        s.append("modal")
    if w.hasFocus():
        s.append("focused")
    return s


def _cell(model, row: int, col: int, parent: QModelIndex) -> str:
    v = model.data(model.index(row, col, parent), Qt.ItemDataRole.DisplayRole)
    return "" if v is None else " ".join(str(v).split())[:MAX_TEXT]


def _items(view: QAbstractItemView, owner: str) -> tuple[list[Node], dict[str, Any]]:
    """Items of a list/table/tree, read from the model (never from cell widgets)."""
    model = view.model()
    if model is None:
        return [], {"rows": 0}
    root = view.rootIndex()
    rows = model.rowCount(root)
    columned = isinstance(view, (QTableView, QTreeView))
    # QAbstractListModel makes columnCount private; list views show one column anyway.
    cols = model.columnCount(root) if columned else 1
    shown_cols = [c for c in range(cols) if not (columned and view.isColumnHidden(c))]
    if not columned:
        name_col = getattr(view, "modelColumn", lambda: 0)()
        value_cols: list[int] = []
    else:
        name_col = shown_cols[0] if shown_cols else 0
        value_cols = [c for c in shown_cols if c != name_col][:MAX_COLUMNS - 1]
    sel = view.selectionModel()

    first = view.indexAt(view.viewport().rect().topLeft())
    start = first.row() if first.isValid() and first.parent() == root else 0

    out: list[Node] = []

    def add(row: int, parent: QModelIndex, depth: int) -> None:
        if len(out) >= MAX_ITEMS:
            return
        idx = model.index(row, name_col, parent)
        values = [_cell(model, row, c, parent) for c in value_cols]
        state = []
        if sel is not None and sel.isSelected(idx):
            state.append("selected")
        if not (model.flags(idx) & Qt.ItemFlag.ItemIsEnabled):
            state.append("disabled")
        node = Node("item", _cell(model, row, name_col, parent),
                    value=" | ".join(values) if any(values) else None, state=state,
                    owner=owner, widget=view, index=QPersistentModelIndex(idx))
        if depth:
            node.info["depth"] = depth
        expanded = isinstance(view, QTreeView) and model.hasChildren(idx) and view.isExpanded(idx)
        if isinstance(view, QTreeView) and model.hasChildren(idx):
            node.state.append("expanded" if expanded else "collapsed")
        out.append(node)
        if expanded and depth < MAX_TREE_DEPTH:
            for r in range(model.rowCount(idx)):
                add(r, idx, depth + 1)

    for r in range(start, rows):
        if len(out) >= MAX_ITEMS:
            break
        add(r, root, 0)
    info: dict[str, Any] = {"rows": rows}
    top = sum(1 for n in out if not n.info.get("depth"))
    if start > 0 or start + top < rows:
        info["showing"] = f"{start + 1}-{start + top}" if top else "none"
    return out, info


# --- building ---------------------------------------------------------------------------

@dataclass
class _Ctx:
    owner: str = ""
    #: ids of QLabels that name a field (dropped from the outline: the field carries the name).
    consumed: set[int] = field(default_factory=set)


def _node_for(w: QWidget, role: str, ctx: _Ctx) -> Node:
    name = accessible_name(w)
    node = Node(role, name, value=_text_value(w), state=_state(w, role), owner=ctx.owner,
                widget=w, hint="" if name else w.objectName())
    if role in ("field", "combo", "slider", "list", "table", "tree"):
        label = labelled_by(w)
        if label is not None:
            ctx.consumed.add(id(label))
    if isinstance(w, QComboBox):
        node.info["options"] = w.count()
    if is_describable(w):
        try:
            node.info.update(dict(w.agent_describe() or {}))
        except Exception as e:  # noqa: BLE001 - a broken describer must not break the snapshot
            node.info["describe_error"] = str(e)[:100]
    return node


def _walk_children(w: QWidget, parent: Node, ctx: _Ctx) -> None:
    for child in w.children():
        if isinstance(child, QWidget) and not child.isWindow():
            _walk(child, parent, ctx)


def _tabs(tabbar: QTabBar, parent: Node, ctx: _Ctx, tabwidget: QTabWidget | None) -> None:
    owner_widget = tabwidget or tabbar
    holder = Node("tabs", accessible_name(owner_widget), owner=ctx.owner, widget=owner_widget,
                  hint="" if accessible_name(owner_widget) else owner_widget.objectName())
    for i in range(tabbar.count()):
        if not tabbar.isTabVisible(i):
            continue
        state = []
        if i == tabbar.currentIndex():
            state.append("selected")
        if not tabbar.isTabEnabled(i):
            state.append("disabled")
        tab = Node("tab", clean(tabbar.tabText(i)) or clean(tabbar.tabToolTip(i)), state=state,
                   owner=ctx.owner, widget=tabbar, tab=i)
        holder.children.append(tab)
        if tabwidget is not None and i == tabbar.currentIndex() and tabwidget.currentWidget():
            _walk(tabwidget.currentWidget(), tab, ctx)
    parent.children.append(holder)


def _menu_items(menu: QWidget, node: Node, ctx: _Ctx) -> None:
    for action in menu.actions():
        if action.isSeparator() or not action.isVisible():
            continue
        state = [] if action.isEnabled() else ["disabled"]
        if action.isCheckable() and action.isChecked():
            state.append("checked")
        if action.menu() is not None:
            state.append("submenu")
        node.children.append(Node("menuitem", clean(action.text()), state=state,
                                  owner=ctx.owner, widget=menu, action=action))


def _walk(w: QWidget, parent: Node, ctx: _Ctx) -> None:
    if not _visible(w) or isinstance(w, _SKIPPED):
        return
    if isinstance(w, QTabWidget):
        _tabs(w.tabBar(), parent, ctx, w)
        return
    if isinstance(w, QTabBar):
        if not isinstance(w.parentWidget(), QTabWidget):
            _tabs(w, parent, ctx, None)
        return
    role = role_of(w)
    if role is None:
        _walk_children(w, parent, ctx)
        return
    node = _node_for(w, role, ctx)
    parent.children.append(node)
    if role in ("list", "table", "tree"):
        node.children, info = _items(w, ctx.owner)
        node.info.update(info)
        return
    if role in LEAVES or node.info.get("opaque"):
        return
    if role in ("dock", "dialog", "window", "menu") and node.name:
        ctx = _Ctx(owner=node.name, consumed=ctx.consumed)
    if role in ("menu", "menubar"):
        _menu_items(w, node, ctx)
        return
    _walk_children(w, node, ctx)


def _prune(nodes: list[Node], consumed: set[int]) -> list[Node]:
    out = []
    for n in nodes:
        if n.role == "label" and (id(n.widget) in consumed or not n.name):
            continue
        n.children = _prune(n.children, consumed)
        out.append(n)
    return out


def build_root(w: QWidget) -> Node:
    """The full node tree of one top-level window."""
    if is_toast(w):
        return Node("toast", clean(str(w.getTitle() or "")),
                    value=clean(str(w.getText() or "")) or None, widget=w)
    ctx = _Ctx()
    holder = Node("holder", "")
    role = role_of(w) or "window"
    if role_of(w) is None or role in LEAVES:
        # A bare top-level widget (tests, tool windows): treat it as a window around itself.
        root = Node("window", accessible_name(w), widget=w, state=_state(w, "window"))
        if role in LEAVES:
            _walk(w, root, ctx)
        else:
            _walk_children(w, root, _Ctx(owner=root.name, consumed=ctx.consumed))
    else:
        _walk(w, holder, ctx)
        root = holder.children[0] if holder.children else Node(role, accessible_name(w), widget=w)
    root.children = _prune(root.children, ctx.consumed)
    return root


def build_all() -> list[Node]:
    """Node trees of every visible top-level window, in z-order."""
    return [build_root(w) for w in roots()]


def interactive_count(node: Node) -> int:
    return sum(1 for n in node.walk() if n is not node and n.role in INTERACTIVE)
