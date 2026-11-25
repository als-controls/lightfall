"""ui_act: operate a widget with real mouse and keyboard input.

Input goes through ``QTest`` so the widget's own event handling runs (focus changes,
``editingFinished``, key handling), not a method call that skips them.

``act()`` runs on a worker thread (the agent's loop). It resolves the target in one GUI
call, *posts* the input with ``QTimer.singleShot(0, ...)``, then polls from the worker
until the UI settles. Posting matters: a click whose slot opens ``QDialog.exec()`` runs a
nested event loop and does not return until the dialog closes, so performing the input
inside a blocking GUI call would hang the agent. ``act_sync()`` performs the input
directly for GUI-thread callers that know no modal loop will start (tests, the audit).
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any

import shiboken6
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QLineEdit,
    QMenu,
    QMenuBar,
    QPlainTextEdit,
    QRadioButton,
    QScrollArea,
    QTabBar,
    QTextEdit,
    QWidget,
)

from lightfall.ui.surface.locate import locate
from lightfall.ui.surface.names import accessible_name, clean
from lightfall.ui.surface.roles import role_of
from lightfall.ui.surface.snapshot import set_last_owner
from lightfall.ui.surface.tree import Node, SurfaceError, build_all, is_toast, roots
from lightfall.utils.crash_diagnostics import gui_thread_only

ACTIONS = ("click", "double_click", "right_click", "check", "uncheck", "fill", "select",
           "press", "scroll_to", "open_panel", "close")

#: Actions that need an enabled target.
_NEEDS_ENABLED = {"click", "double_click", "right_click", "check", "uncheck", "fill", "select",
                  "press"}

POLL_S = 0.05
MAX_OPTIONS_LISTED = 20


class ActError(SurfaceError):
    pass


# --- input primitives (GUI thread) ------------------------------------------------------

def _center(w: QWidget) -> QPoint:
    """Where a person would click: the middle, except for check boxes and radio buttons,
    which only react on their indicator and text (a stretched one's middle is empty)."""
    if isinstance(w, (QCheckBox, QRadioButton)):
        x = w.width() - 8 if w.layoutDirection() == Qt.LayoutDirection.RightToLeft else 8
        return QPoint(max(1, min(x, w.width() - 1)), w.height() // 2)
    return w.rect().center()


def _click(w: QWidget, pos: QPoint | None = None, button=Qt.MouseButton.LeftButton) -> None:
    QTest.mouseClick(w, button, Qt.KeyboardModifier.NoModifier, pos or _center(w))


def _double_click(w: QWidget, pos: QPoint | None = None) -> None:
    pos = pos or _center(w)
    QTest.mouseClick(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseDClick(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)


def _right_click(w: QWidget, pos: QPoint | None = None) -> None:
    # QTest mouse events go straight to the widget, so Qt never synthesises the context
    # menu event a real right click produces; send it as the platform would.
    pos = pos or _center(w)
    QTest.mouseClick(w, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, pos)
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, w.mapToGlobal(pos))
    QApplication.sendEvent(w, event)


def _select_all_and_type(w: QWidget, text: str) -> None:
    QTest.keyClick(w, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    if text:
        QTest.keyClicks(w, text)


def _press_sequence(w: QWidget, keys: str) -> None:
    seq = QKeySequence(keys)
    if seq.isEmpty():
        raise ActError(f"cannot parse keys '{keys}' (examples: 'Return', 'Ctrl+S', 'Escape')")
    for i in range(seq.count()):
        combo = seq[i]
        QTest.keyClick(w, combo.key(), combo.keyboardModifiers())


# --- target geometry ---------------------------------------------------------------------

def _item_point(view: QAbstractItemView, index) -> QPoint:
    view.scrollTo(index)
    rect = view.visualRect(index)
    if not rect.isValid():
        raise ActError("the item is not visible in its view (scrolled out or collapsed)")
    return rect.center()


def _tab_point(bar: QTabBar, i: int) -> QPoint:
    return bar.tabRect(i).center()


def _tabbar_of(w: QWidget) -> QTabBar:
    return w if isinstance(w, QTabBar) else w.tabBar()


def _find_item(view: QAbstractItemView, name: str):
    model = view.model()
    if model is None:
        raise ActError("this view has no items")
    start = model.index(0, 0, view.rootIndex())
    for flags in (Qt.MatchFlag.MatchExactly, Qt.MatchFlag.MatchFixedString):
        hits = model.match(start, Qt.ItemDataRole.DisplayRole, name, 2,
                           flags | Qt.MatchFlag.MatchRecursive)
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ActError(f"more than one item named '{name}'; target the item by ref")
    raise ActError(f"no item '{name}' in this view")


# --- per-action plans (GUI thread; return a callable that performs the input) ------------

def _plan_click(node: Node, kind: str) -> Callable[[], Any]:
    fn = {"click": _click, "double_click": _double_click, "right_click": _right_click}[kind]
    w = node.widget
    if node.role == "item":
        return lambda: fn(w.viewport(), _item_point(w, node.index))
    if node.role == "tab":
        return lambda: fn(w, _tab_point(w, node.tab))
    if node.role == "menuitem":
        action = node.action
        if isinstance(w, (QMenu, QMenuBar)):
            return lambda: fn(w, w.actionGeometry(action).center())
    if node.role == "dock":
        return lambda: (w.show(), w.raise_(), w.setFocus())
    return lambda: fn(w)


def _plan_check(node: Node, want: bool) -> Callable[[], Any]:
    w = node.widget
    if not isinstance(w, QAbstractButton) or not w.isCheckable():
        raise ActError(f"{node.label} cannot be checked")
    if w.isChecked() == want:
        return lambda: None
    return lambda: _click(w)


def _plan_fill(node: Node, value: str) -> Callable[[], Any]:
    w = node.widget
    if isinstance(w, QComboBox):
        if not w.isEditable():
            raise ActError(f"{node.label} is a fixed list; use action 'select'")
        w = w.lineEdit()
    if isinstance(w, (QLineEdit, QAbstractSpinBox)):
        if w.isReadOnly():
            raise ActError(f"{node.label} is read-only")

        def fill_line() -> None:
            _click(w)
            _select_all_and_type(w, value)
            QTest.keyClick(w, Qt.Key.Key_Return)
        return fill_line
    if isinstance(w, (QTextEdit, QPlainTextEdit)):
        if w.isReadOnly():
            raise ActError(f"{node.label} is read-only")

        def fill_text() -> None:
            _click(w)
            _select_all_and_type(w, value)
            w.clearFocus()  # commit by focus-out: Return would insert a newline
        return fill_text
    raise ActError(f"{node.label} is not a text field (role {node.role})")


def _plan_select(node: Node, value: str) -> Callable[[], Any]:
    w = node.widget
    if isinstance(w, QComboBox):
        idx = w.findText(value, Qt.MatchFlag.MatchExactly)
        if idx < 0:
            idx = w.findText(value, Qt.MatchFlag.MatchFixedString)
        if idx < 0:
            options = [w.itemText(i) for i in range(min(w.count(), MAX_OPTIONS_LISTED))]
            raise ActError(f"no option '{value}' in {node.label}; options: {options}")

        def choose() -> None:
            # Keyboard selection emits activated(), as a person's choice does, and avoids
            # the popup (unreliable under offscreen platforms).
            QTest.keyClick(w, Qt.Key.Key_Home)
            last = -2
            while w.currentIndex() != idx and w.currentIndex() != last:
                last = w.currentIndex()
                QTest.keyClick(w, Qt.Key.Key_Down)
            if w.currentIndex() != idx:
                raise ActError(f"could not reach option '{value}' (disabled?)")
        return choose
    if node.role in ("list", "table", "tree"):
        index = _find_item(w, value)
        return lambda: _click(w.viewport(), _item_point(w, index))
    if node.role in ("tabs", "tab"):
        bar = _tabbar_of(w)
        names = [clean(bar.tabText(i)) for i in range(bar.count())]
        matches = [i for i, n in enumerate(names) if n.casefold() == value.casefold()]
        if len(matches) != 1:
            raise ActError(f"no single tab '{value}'; tabs: {names}")
        return lambda: _click(bar, _tab_point(bar, matches[0]))
    if node.role in ("menubar", "menu", "window"):
        return _plan_menu_path(node, value)
    raise ActError(f"{node.label} has nothing to select (role {node.role}); use 'click'")


def _plan_menu_path(node: Node, path: str) -> Callable[[], Any]:
    steps = [clean(p) for p in path.split(">") if p.strip()]
    w = node.widget
    bar = w if isinstance(w, (QMenuBar, QMenu)) else w.findChild(QMenuBar)
    if bar is None:
        raise ActError(f"{node.label} has no menu bar")

    def find(container: QWidget, text: str):
        for a in container.actions():
            if not a.isSeparator() and clean(a.text()).casefold() == text.casefold():
                return a
        raise ActError(f"no menu entry '{text}'; entries: "
                       f"{[clean(a.text()) for a in container.actions() if not a.isSeparator()]}")

    def run() -> dict:
        container: QWidget = bar
        for i, step in enumerate(steps):
            action = find(container, step)
            last = i == len(steps) - 1
            if container.isVisible():
                _click(container, container.actionGeometry(action).center())
            elif last:
                action.trigger()
                return {"via": "trigger"}
            if last:
                return {}
            sub = action.menu()
            if sub is None:
                raise ActError(f"'{step}' has no submenu")
            QApplication.processEvents()
            container = sub
        return {}
    return run


def _plan_scroll(node: Node) -> Callable[[], Any]:
    w = node.widget
    if node.role == "item":
        return lambda: w.scrollTo(node.index)
    area = w.parentWidget()
    while area is not None and not isinstance(area, QScrollArea):
        area = area.parentWidget()
    if area is None:
        return lambda: None
    return lambda: area.ensureWidgetVisible(w)


def _plan_close(node: Node) -> Callable[[], Any]:
    w = node.widget
    if isinstance(w, (QDialog, QMenu)) and not is_toast(w):
        return lambda: QTest.keyClick(w, Qt.Key.Key_Escape)
    if isinstance(w, QDockWidget):
        return w.close
    if is_toast(w):
        return w.hide
    raise ActError(f"{node.label} cannot be closed; close its dialog or panel instead")


def _strip_buttons() -> list[Node]:
    out = []
    for root in build_all():
        for n in root.walk():
            if n.widget is not None and type(n.widget).__name__ == "IconStripButton":
                out.append(n)
    return out


def _plan_open_panel(name: str) -> tuple[Node, Callable[[], Any]]:
    wanted = name.strip().casefold()
    buttons = _strip_buttons()
    hits = [b for b in buttons if b.name.casefold() == wanted] or \
           [b for b in buttons if b.name.casefold().startswith(wanted)]
    if len(hits) == 1:
        b = hits[0]
        if b.widget.isChecked():
            return b, lambda: {"already_open": True}
        return b, lambda: _click(b.widget)
    if len(hits) > 1:
        raise ActError(f"'{name}' matches several panels: {[b.name for b in hits]}")
    raise ActError(f"no panel '{name}' in the sidebar; panels: {[b.name for b in buttons]}")


# --- probes and diff ---------------------------------------------------------------------

def _recent_toasts(since: float) -> list[dict]:
    try:
        from lightfall.ui.toast import ToastManager

        recent = getattr(ToastManager.get_instance(), "recent", None)
        return recent(since) if callable(recent) else []
    except Exception:  # noqa: BLE001
        return []


def _window_label(w: QWidget) -> str:
    if is_toast(w):
        return f'toast "{clean(str(w.getTitle() or ""))}"'
    return f'{role_of(w) or "window"} "{accessible_name(w)}"'


@gui_thread_only
def _probe() -> dict:
    tops = {id(w): _window_label(w) for w in roots()}
    focus = QApplication.focusWidget()
    modal = QApplication.activeModalWidget()
    return {"tops": tops,
            "focus": id(focus) if focus else 0,
            "focus_label": f'{role_of(focus) or "widget"} "{accessible_name(focus)}"' if focus else "",
            "modal": id(modal) if modal else 0}


def _target_state(node: Node) -> dict:
    w = node.widget
    if w is None or not shiboken6.isValid(w):
        return {"gone": True}
    from lightfall.ui.surface.tree import _state, _text_value

    out: dict[str, Any] = {}
    if node.role == "item" and node.index is not None and node.index.isValid():
        sel = w.selectionModel()
        out["selected"] = bool(sel and sel.isSelected(node.index))
        return out
    if node.role == "tab":
        out["selected"] = _tabbar_of(w).currentIndex() == node.tab
        return out
    value = _text_value(w)
    if value is not None:
        out["value"] = value
    role = role_of(w) or node.role
    out["state"] = [s for s in _state(w, role) if s != "focused"]
    return out


def _diff(before: dict, after: dict, t0: float, tb: dict, ta: dict) -> dict:
    changes: dict[str, Any] = {}
    opened = [lbl for k, lbl in after["tops"].items() if k not in before["tops"]]
    closed = [lbl for k, lbl in before["tops"].items() if k not in after["tops"]]
    if opened:
        changes["opened"] = opened
    if closed:
        changes["closed"] = closed
    toasts = _recent_toasts(t0)
    if toasts:
        changes["toasts"] = [f'{t.get("kind", "info")} "{t.get("title", "")}"'
                             + (f': {t["text"]}' if t.get("text") else "") for t in toasts]
    if after["focus"] != before["focus"] and after["focus_label"]:
        changes["focus"] = after["focus_label"]
    target = {k: v for k, v in ta.items() if tb.get(k) != v}
    if target:
        changes["target"] = target
    return changes


# --- prepare / run -----------------------------------------------------------------------

@gui_thread_only
def _prepare(action: str, target, value) -> dict:
    if action not in ACTIONS:
        raise ActError(f"unknown action '{action}'; actions: {', '.join(ACTIONS)}")
    if action == "open_panel":
        name = value if value else (target if isinstance(target, str) else (target or {}).get("name"))
        if not name:
            raise ActError("open_panel needs the panel name")
        node, perform = _plan_open_panel(str(name))
    else:
        if target in (None, "", {}):
            if action != "press":
                raise ActError(f"'{action}' needs a target")
            focus = QApplication.focusWidget()
            if focus is None:
                raise ActError("nothing has keyboard focus; give 'press' a target")
            node = Node(role_of(focus) or "widget", accessible_name(focus), widget=focus)
        else:
            node = locate(target)
        w = node.widget
        if w is None or not w.isVisible():
            raise ActError(f"{node.label} is not visible")
        if action in _NEEDS_ENABLED and (not w.isEnabled() or "disabled" in node.state):
            raise ActError(f"{node.label} is disabled")
        if action in ("click", "double_click", "right_click"):
            perform = _plan_click(node, action)
        elif action in ("check", "uncheck"):
            perform = _plan_check(node, action == "check")
        elif action == "fill":
            if value is None:
                raise ActError("'fill' needs a value")
            perform = _plan_fill(node, str(value))
        elif action == "select":
            if value is None and node.role not in ("item", "tab", "menuitem"):
                raise ActError("'select' needs a value (option, item, tab or 'Menu > Entry')")
            perform = _plan_click(node, "click") if value is None else _plan_select(node, str(value))
        elif action == "press":
            if not value:
                raise ActError("'press' needs keys, e.g. 'Return' or 'Ctrl+S'")
            perform = functools.partial(_press_sequence, w, str(value))
        elif action == "scroll_to":
            perform = _plan_scroll(node)
        else:
            perform = _plan_close(node)
    set_last_owner(node.owner or (node.name if node.role == "dock" else ""))
    return {"perform": perform, "node": node, "label": node.label, "probe": _probe(),
            "target": _target_state(node)}


def _finish(prep: dict, t0: float, holder: dict, settled: bool) -> dict:
    after = _probe()
    ta = _target_state(prep["node"])
    result: dict[str, Any] = {"ok": holder.get("error") is None, "target": prep["label"],
                              "changes": _diff(prep["probe"], after, t0, prep["target"], ta)}
    if isinstance(holder.get("result"), dict) and holder["result"]:
        result.update(holder["result"])
    if not settled:
        result["settled"] = False
    if holder.get("error") is not None:
        result["error"] = holder["error"]
    return result


def act(action: str, target=None, value=None, *, settle_ms: int = 150,
        timeout_s: float = 10.0) -> dict:
    """Operate a widget. Call from a worker thread (the agent loop), never the GUI thread."""
    from lightfall.claude._internal.threading import run_on_main_thread
    from lightfall.utils.threads import is_main_thread

    if is_main_thread():
        raise RuntimeError("act() must run off the GUI thread; use act_sync() there")
    t0 = time.monotonic()
    try:
        prep = run_on_main_thread(_prepare, action, target, value)
    except RuntimeError as e:  # run_on_main_thread wraps exceptions with their traceback
        raise ActError(str(e).split("\n", 1)[0]) from None
    holder: dict[str, Any] = {"started": False, "done": False}

    def perform() -> None:
        holder["started"] = True
        try:
            holder["result"] = prep["perform"]()
        except SurfaceError as e:
            holder["error"] = str(e)
        except Exception as e:  # noqa: BLE001 - report, never crash the GUI
            holder["error"] = f"{type(e).__name__}: {e}"
        finally:
            holder["done"] = True

    run_on_main_thread(lambda: QTimer.singleShot(0, perform))
    settled = _settle(holder, settle_ms, timeout_s)
    return run_on_main_thread(_finish, prep, t0, holder, settled)


def _settle(holder: dict, settle_ms: int, timeout_s: float) -> bool:
    """Poll until the input ran and the UI stopped changing for *settle_ms*."""
    from lightfall.claude._internal.threading import run_on_main_thread

    deadline = time.monotonic() + timeout_s
    last, stable_since = None, None
    while time.monotonic() < deadline:
        time.sleep(POLL_S)
        if not holder["started"]:
            continue
        probe = run_on_main_thread(_probe)
        key = (tuple(sorted(probe["tops"])), probe["focus"], probe["modal"], holder["done"])
        now = time.monotonic()
        if key != last:
            last, stable_since = key, now
        elif now - stable_since >= settle_ms / 1000:
            return True
    return False


@gui_thread_only
def act_sync(action: str, target=None, value=None, *, settle_ms: int = 100) -> dict:
    """GUI-thread variant: performs the input directly. Must not open a modal exec()."""
    t0 = time.monotonic()
    prep = _prepare(action, target, value)
    holder: dict[str, Any] = {}
    try:
        holder["result"] = prep["perform"]()
    except SurfaceError as e:
        holder["error"] = str(e)
    end = time.monotonic() + settle_ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    return _finish(prep, t0, holder, True)

