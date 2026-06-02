# Theater Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a generic "theater mode" that lets any QWidget expand into an animated overlay covering the main window, then collapse back — primarily for PyQtGraph plots and ImageViews.

**Architecture:** Three classes in `lightfall/ui/theater/`: TheaterProxy (QStackedWidget wrapper with hover expand button), TheaterOverlay (dimmed backdrop + expanded widget), TheaterManager (singleton coordinator). The proxy holds the widget normally; on expand, the overlay takes it via reparenting; on collapse, the proxy gets it back. Animated open/close with parallel geometry + opacity transitions.

**Tech Stack:** PySide6 (QStackedWidget, QPropertyAnimation, QParallelAnimationGroup, Property), qtawesome for icons, pytest-qt for testing.

**Spec:** `docs/superpowers/specs/2026-04-10-theater-mode-design.md`

---

## File Structure

```
src/lightfall/ui/theater/
├── __init__.py      # Package exports: TheaterProxy, TheaterOverlay, TheaterManager, theater_manager
├── proxy.py         # TheaterProxy(QStackedWidget) — wraps widget, hover button, page switching
├── overlay.py       # TheaterOverlay(QWidget) — dimmed backdrop, expanded widget, animations
└── manager.py       # TheaterManager — singleton, install/uninstall, activate delegation

tests/theater/
├── __init__.py      # Empty
├── conftest.py      # Shared fixtures (parent_widget, manager reset)
├── test_proxy.py    # TheaterProxy tests
├── test_overlay.py  # TheaterOverlay tests
└── test_manager.py  # TheaterManager tests
```

---

## Task 1: Package skeleton + TheaterProxy core

**Files:**
- Create: `src/lightfall/ui/theater/__init__.py`
- Create: `src/lightfall/ui/theater/proxy.py`
- Create: `src/lightfall/ui/theater/manager.py` (stub only — needed for proxy auto-registration)
- Create: `tests/theater/__init__.py`
- Create: `tests/theater/conftest.py`
- Create: `tests/theater/test_proxy.py`

- [ ] **Step 1: Create package skeleton and manager stub**

Create the theater package directory and a minimal manager stub so the proxy can import it.

`src/lightfall/ui/theater/__init__.py`:
```python
"""Theater mode — widget expansion overlay."""
```

`src/lightfall/ui/theater/manager.py`:
```python
"""TheaterManager — singleton coordinator for theater mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lightfall.ui.theater.proxy import TheaterProxy


class TheaterManager:
    """Coordinates TheaterProxy instances and the TheaterOverlay."""

    def __init__(self) -> None:
        self._proxies: dict[int, TheaterProxy] = {}
        self._overlay = None

    def register(self, proxy: TheaterProxy) -> None:
        """Register a proxy and connect its expand signal."""
        widget_id = id(proxy.target_widget)
        self._proxies[widget_id] = proxy
        proxy.expand_requested.connect(lambda: self.activate(proxy))

    def unregister(self, proxy: TheaterProxy) -> None:
        """Unregister a proxy."""
        widget_id = id(proxy.target_widget)
        self._proxies.pop(widget_id, None)

    def activate(self, proxy: TheaterProxy) -> None:
        """Expand a proxy's widget onto the overlay (stub)."""

    def deactivate(self) -> None:
        """Collapse the currently expanded widget (stub)."""


theater_manager = TheaterManager()
```

- [ ] **Step 2: Create test infrastructure**

`tests/theater/__init__.py`: empty file.

`tests/theater/conftest.py`:
```python
"""Shared fixtures for theater mode tests."""

import pytest
from PySide6.QtWidgets import QVBoxLayout, QWidget


@pytest.fixture()
def parent_widget(qtbot):
    """A parent widget with a layout, simulating a panel interior."""
    w = QWidget()
    w.setObjectName("TestParent")
    w.resize(800, 600)
    QVBoxLayout(w)
    qtbot.addWidget(w)
    w.show()
    return w


@pytest.fixture(autouse=True)
def _reset_theater_manager():
    """Reset the theater manager singleton between tests."""
    from lightfall.ui.theater.manager import theater_manager

    theater_manager._proxies.clear()
    theater_manager._overlay = None
    yield
    theater_manager._proxies.clear()
    theater_manager._overlay = None
```

- [ ] **Step 3: Write failing tests for TheaterProxy core**

`tests/theater/test_proxy.py`:
```python
"""Tests for TheaterProxy page switching and widget handoff."""

from PySide6.QtWidgets import QLabel, QWidget

from lightfall.ui.theater.proxy import TheaterProxy


class TestTheaterProxyCore:
    """Core page switching: init, take_widget, return_widget."""

    def test_init_shows_target_as_current(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        assert proxy.count() == 2
        assert proxy.currentIndex() == 0
        assert proxy.currentWidget() is target

    def test_target_widget_property(self, qtbot):
        target = QLabel("plot")
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        assert proxy.target_widget is target

    def test_take_widget_switches_to_placeholder(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        taken = proxy.take_widget()

        assert taken is target
        assert proxy.currentIndex() == 0  # placeholder is now index 0
        assert proxy.currentWidget() is not target

    def test_return_widget_restores_target(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        taken = proxy.take_widget()
        proxy.return_widget(taken)

        assert proxy.currentIndex() == 0
        assert proxy.currentWidget() is target

    def test_take_return_roundtrip_preserves_count(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        assert proxy.count() == 2
        proxy.take_widget()
        assert proxy.count() == 1  # target removed from stack
        proxy.return_widget(target)
        assert proxy.count() == 2  # target re-inserted

    def test_auto_registers_with_manager(self, qtbot):
        from lightfall.ui.theater.manager import theater_manager

        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        assert id(target) in theater_manager._proxies
        assert theater_manager._proxies[id(target)] is proxy
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/test_proxy.py -v`

Expected: FAIL — `ModuleNotFoundError` because `proxy.py` does not exist yet.

- [ ] **Step 5: Implement TheaterProxy core**

`src/lightfall/ui/theater/proxy.py`:
```python
"""TheaterProxy — QStackedWidget wrapper for theater mode."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QStackedWidget, QToolButton, QWidget

try:
    import qtawesome as qta
except ImportError:  # pragma: no cover
    qta = None


class TheaterProxy(QStackedWidget):
    """Wraps a widget for theater mode expansion.

    Page 0: target widget (normal display).
    Page 1: placeholder shown while the widget is on the overlay.
    """

    expand_requested = Signal()

    def __init__(self, widget: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target = widget
        self.setObjectName("TheaterProxy")

        # Page 0 — target widget
        self.addWidget(widget)

        # Page 1 — placeholder
        self._placeholder = QLabel("Expanded")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setObjectName("TheaterPlaceholder")
        self.addWidget(self._placeholder)

        self.setCurrentIndex(0)

        # Hover expand button (hidden by default, configured in Task 2)
        self._expand_btn = QToolButton(self)
        self._expand_btn.setFixedSize(24, 24)
        self._expand_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self._expand_btn.setObjectName("TheaterExpandButton")
        self._expand_btn.setVisible(False)
        self._expand_btn.clicked.connect(self.expand_requested.emit)

        # Register with theater manager
        from lightfall.ui.theater.manager import theater_manager

        theater_manager.register(self)

    @property
    def target_widget(self) -> QWidget:
        """The widget wrapped by this proxy."""
        return self._target

    def take_widget(self) -> QWidget:
        """Remove the target widget and show the placeholder."""
        self.removeWidget(self._target)
        self.setCurrentIndex(0)  # placeholder is now index 0
        return self._target

    def return_widget(self, widget: QWidget) -> None:
        """Return the target widget and show it."""
        self.insertWidget(0, widget)
        self.setCurrentIndex(0)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/test_proxy.py -v`

Expected: All 6 tests PASS.

- [ ] **Step 7: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lightfall/ui/theater/__init__.py src/lightfall/ui/theater/proxy.py src/lightfall/ui/theater/manager.py tests/theater/__init__.py tests/theater/conftest.py tests/theater/test_proxy.py
git commit -m "feat(theater): TheaterProxy core — page switching and widget handoff"
```

---

## Task 2: TheaterProxy hover expand button

**Files:**
- Modify: `src/lightfall/ui/theater/proxy.py`
- Modify: `tests/theater/test_proxy.py`

- [ ] **Step 1: Write failing tests for hover button**

Append to `tests/theater/test_proxy.py`:
```python
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QEnterEvent


class TestTheaterProxyHoverButton:
    """Hover expand button visibility and signal."""

    def test_button_hidden_by_default(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.show()

        assert not proxy._expand_btn.isVisible()

    def test_button_visible_on_enter(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.show()

        event = QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10))
        proxy.enterEvent(event)

        assert proxy._expand_btn.isVisible()

    def test_button_hidden_on_leave(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.show()

        # Enter then leave
        enter = QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10))
        proxy.enterEvent(enter)
        leave = QEvent(QEvent.Type.Leave)
        proxy.leaveEvent(leave)

        assert not proxy._expand_btn.isVisible()

    def test_button_hidden_when_widget_taken(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.show()

        proxy.take_widget()
        enter = QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10))
        proxy.enterEvent(enter)

        assert not proxy._expand_btn.isVisible()

    def test_button_click_emits_expand_requested(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.show()

        with qtbot.waitSignal(proxy.expand_requested, timeout=1000):
            proxy._expand_btn.click()

    def test_button_positioned_top_right_on_resize(self, qtbot):
        target = QWidget()
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)
        proxy.resize(400, 300)
        proxy.show()

        btn = proxy._expand_btn
        margin = 4
        assert btn.x() == 400 - btn.width() - margin
        assert btn.y() == margin
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/test_proxy.py::TestTheaterProxyHoverButton -v`

Expected: FAIL — `enterEvent`/`leaveEvent`/`resizeEvent` not yet implemented.

- [ ] **Step 3: Implement hover button behavior**

Add these methods to the `TheaterProxy` class in `src/lightfall/ui/theater/proxy.py`:

After the `__init__` method, update the button styling and icon:
```python
        self._expand_btn.setStyleSheet(
            "QToolButton { background: rgba(0,0,0,120); border: none; "
            "border-radius: 4px; padding: 2px; }"
            "QToolButton:hover { background: rgba(0,0,0,180); }"
        )
        if qta is not None:
            try:
                self._expand_btn.setIcon(
                    qta.icon("mdi6.arrow-expand-all", color="#e0e0e0")
                )
            except Exception:
                self._expand_btn.setText("\u26f6")
        else:
            self._expand_btn.setText("\u26f6")
```

Add event overrides at the bottom of the class:
```python
    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        if self.currentWidget() is self._target:
            self._expand_btn.setVisible(True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._expand_btn.setVisible(False)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        margin = 4
        self._expand_btn.move(
            self.width() - self._expand_btn.width() - margin,
            margin,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/test_proxy.py -v`

Expected: All 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lightfall/ui/theater/proxy.py tests/theater/test_proxy.py
git commit -m "feat(theater): TheaterProxy hover expand button"
```

---

## Task 3: TheaterOverlay — structure, backdrop, and activate/deactivate

**Files:**
- Create: `src/lightfall/ui/theater/overlay.py`
- Create: `tests/theater/test_overlay.py`

- [ ] **Step 1: Write failing tests for overlay core**

`tests/theater/test_overlay.py`:
```python
"""Tests for TheaterOverlay."""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from lightfall.ui.theater.overlay import TheaterOverlay
from lightfall.ui.theater.proxy import TheaterProxy


class TestTheaterOverlayCore:
    """Overlay creation, backdrop, activate/deactivate."""

    def test_starts_hidden(self, parent_widget, qtbot):
        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        assert not overlay.isVisible()

    def test_backdrop_opacity_property(self, parent_widget, qtbot):
        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        assert overlay.backdrop_opacity == 0
        overlay.backdrop_opacity = 100
        assert overlay.backdrop_opacity == 100

    def test_activate_shows_overlay(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        overlay.activate(proxy)

        # Wait for activation animation to finish
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert overlay.isVisible()
        assert overlay._active_proxy is proxy
        assert overlay._active_widget is target

    def test_activate_reparents_widget_to_overlay(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        overlay.activate(proxy)

        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert target.parentWidget() is overlay

    def test_deactivate_returns_widget_to_proxy(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        # Activate
        overlay.activate(proxy)
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        # Deactivate
        overlay.deactivate()
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert not overlay.isVisible()
        assert overlay._active_proxy is None
        assert proxy.currentWidget() is target

    def test_deactivate_noop_when_inactive(self, parent_widget, qtbot):
        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        overlay.deactivate()  # should not raise

    def test_only_one_widget_at_a_time(self, parent_widget, qtbot):
        target1 = QLabel("plot1")
        target2 = QLabel("plot2")
        parent_widget.layout().addWidget(target1)
        parent_widget.layout().addWidget(target2)
        proxy1 = TheaterProxy(target1)
        proxy2 = TheaterProxy(target2)
        parent_widget.layout().addWidget(proxy1)
        parent_widget.layout().addWidget(proxy2)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        # Activate first
        overlay.activate(proxy1)
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        # Activate second — first should be returned
        overlay.activate(proxy2)
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert overlay._active_proxy is proxy2
        assert proxy1.currentWidget() is target1  # returned
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/test_overlay.py -v`

Expected: FAIL — `overlay.py` does not exist yet.

- [ ] **Step 3: Implement TheaterOverlay**

`src/lightfall/ui/theater/overlay.py`:
```python
"""TheaterOverlay — fullscreen expansion overlay with dimmed backdrop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
)
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QToolButton, QWidget

try:
    import qtawesome as qta
except ImportError:  # pragma: no cover
    qta = None

if TYPE_CHECKING:
    from lightfall.ui.theater.proxy import TheaterProxy


class TheaterOverlay(QWidget):
    """Overlay that displays an expanded widget with a dimmed backdrop."""

    _MARGIN = 20
    _ANIM_DURATION_BACKDROP = 200
    _ANIM_DURATION_GEOMETRY = 300

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TheaterOverlay")

        self._active_proxy: TheaterProxy | None = None
        self._active_widget: QWidget | None = None
        self._backdrop_opacity_value: int = 0
        self._is_animating: bool = False
        self._anim_group: QParallelAnimationGroup | None = None

        # Collapse button
        self._collapse_btn = QToolButton(self)
        self._collapse_btn.setFixedSize(28, 28)
        self._collapse_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self._collapse_btn.setObjectName("TheaterCollapseButton")
        self._collapse_btn.setStyleSheet(
            "QToolButton { background: rgba(0,0,0,160); border: none; "
            "border-radius: 4px; padding: 2px; }"
            "QToolButton:hover { background: rgba(0,0,0,220); }"
        )
        if qta is not None:
            try:
                self._collapse_btn.setIcon(
                    qta.icon("mdi6.arrow-collapse-all", color="#e0e0e0")
                )
            except Exception:
                self._collapse_btn.setText("\u2716")
        else:
            self._collapse_btn.setText("\u2716")
        self._collapse_btn.clicked.connect(self.deactivate)
        self._collapse_btn.setVisible(False)

        # Escape shortcut (works even when child widgets have focus)
        self._escape_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Escape), self
        )
        self._escape_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self._escape_shortcut.activated.connect(self.deactivate)

        self.setVisible(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Track parent resize
        parent.installEventFilter(self)

    # -- backdrop_opacity property for QPropertyAnimation -----------------

    def _get_backdrop_opacity(self) -> int:
        return self._backdrop_opacity_value

    def _set_backdrop_opacity(self, value: int) -> None:
        self._backdrop_opacity_value = value
        self.update()

    backdrop_opacity = Property(int, _get_backdrop_opacity, _set_backdrop_opacity)

    # -- public API -------------------------------------------------------

    def activate(self, proxy: TheaterProxy) -> None:
        """Expand the proxy's widget onto the overlay."""
        if self._is_animating:
            return
        if self._active_proxy is not None:
            self._finish_deactivate()

        self._active_proxy = proxy
        widget = proxy.take_widget()
        self._active_widget = widget

        # Resize overlay to fill parent
        parent = self.parentWidget()
        self.setGeometry(0, 0, parent.width(), parent.height())

        # Capture origin rect (proxy position in overlay coords)
        origin = self.mapFromGlobal(proxy.mapToGlobal(QPoint(0, 0)))
        origin_rect = QRect(origin, proxy.size())
        target_rect = self._expanded_rect()

        # Reparent widget into overlay
        widget.setParent(self)
        widget.setGeometry(origin_rect)
        widget.show()

        # Show overlay
        self._backdrop_opacity_value = 0
        self.setVisible(True)
        self.raise_()
        self.setFocus()
        self._collapse_btn.setVisible(True)
        self._collapse_btn.raise_()
        self._update_collapse_btn_position(target_rect)

        # Animate
        self._is_animating = True
        self._animate_open(origin_rect, target_rect, widget)

    def deactivate(self) -> None:
        """Collapse the widget back to its proxy."""
        if self._is_animating or self._active_proxy is None:
            return

        proxy = self._active_proxy
        widget = self._active_widget

        # Recapture proxy position (may have changed during resize)
        target_origin = self.mapFromGlobal(proxy.mapToGlobal(QPoint(0, 0)))
        target_rect = QRect(target_origin, proxy.size())
        current_rect = widget.geometry()

        self._collapse_btn.setVisible(False)
        self._is_animating = True
        self._animate_close(current_rect, target_rect, widget)

    # -- animation --------------------------------------------------------

    def _animate_open(
        self, origin: QRect, target: QRect, widget: QWidget
    ) -> None:
        backdrop_anim = QPropertyAnimation(self, b"backdrop_opacity")
        backdrop_anim.setDuration(self._ANIM_DURATION_BACKDROP)
        backdrop_anim.setStartValue(0)
        backdrop_anim.setEndValue(150)
        backdrop_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        geo_anim = QPropertyAnimation(widget, b"geometry")
        geo_anim.setDuration(self._ANIM_DURATION_GEOMETRY)
        geo_anim.setStartValue(origin)
        geo_anim.setEndValue(target)
        geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group = QParallelAnimationGroup(self)
        self._anim_group.addAnimation(backdrop_anim)
        self._anim_group.addAnimation(geo_anim)
        self._anim_group.finished.connect(self._on_activate_finished)
        self._anim_group.start()

    def _animate_close(
        self, current: QRect, target: QRect, widget: QWidget
    ) -> None:
        backdrop_anim = QPropertyAnimation(self, b"backdrop_opacity")
        backdrop_anim.setDuration(self._ANIM_DURATION_BACKDROP)
        backdrop_anim.setStartValue(self._backdrop_opacity_value)
        backdrop_anim.setEndValue(0)
        backdrop_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        geo_anim = QPropertyAnimation(widget, b"geometry")
        geo_anim.setDuration(self._ANIM_DURATION_GEOMETRY)
        geo_anim.setStartValue(current)
        geo_anim.setEndValue(target)
        geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group = QParallelAnimationGroup(self)
        self._anim_group.addAnimation(backdrop_anim)
        self._anim_group.addAnimation(geo_anim)
        self._anim_group.finished.connect(self._finish_deactivate)
        self._anim_group.start()

    def _on_activate_finished(self) -> None:
        self._is_animating = False

    def _finish_deactivate(self) -> None:
        """Return the widget to its proxy and hide the overlay."""
        self._is_animating = False
        if self._active_proxy is None:
            return
        proxy = self._active_proxy
        widget = self._active_widget
        proxy.return_widget(widget)
        self._active_proxy = None
        self._active_widget = None
        self._collapse_btn.setVisible(False)
        self.setVisible(False)

    # -- geometry helpers -------------------------------------------------

    def _expanded_rect(self) -> QRect:
        return QRect(
            self._MARGIN,
            self._MARGIN,
            self.width() - 2 * self._MARGIN,
            self.height() - 2 * self._MARGIN,
        )

    def _update_collapse_btn_position(
        self, content_rect: QRect | None = None
    ) -> None:
        if content_rect is None and self._active_widget is not None:
            content_rect = self._active_widget.geometry()
        if content_rect is None:
            return
        self._collapse_btn.move(
            content_rect.right() - self._collapse_btn.width() - 8,
            content_rect.top() + 8,
        )

    # -- events -----------------------------------------------------------

    def paintEvent(self, event) -> None:
        if self._backdrop_opacity_value > 0:
            painter = QPainter(self)
            painter.fillRect(
                self.rect(), QColor(0, 0, 0, self._backdrop_opacity_value)
            )
            painter.end()

    def keyPressEvent(self, event) -> None:
        """Fallback Escape handler when overlay itself has focus."""
        if event.key() == Qt.Key.Key_Escape:
            self.deactivate()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        """Click on backdrop (outside widget) dismisses the overlay."""
        if self._active_widget is not None:
            if not self._active_widget.geometry().contains(event.pos()):
                self.deactivate()
                return
        super().mousePressEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            new_size = event.size()
            self.setGeometry(0, 0, new_size.width(), new_size.height())
            if self._active_widget is not None and not self._is_animating:
                target_rect = self._expanded_rect()
                self._active_widget.setGeometry(target_rect)
                self._update_collapse_btn_position(target_rect)
        return super().eventFilter(obj, event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/test_overlay.py -v`

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lightfall/ui/theater/overlay.py tests/theater/test_overlay.py
git commit -m "feat(theater): TheaterOverlay — backdrop, activate/deactivate with animation"
```

---

## Task 4: TheaterOverlay dismissal triggers

**Files:**
- Modify: `tests/theater/test_overlay.py`

- [ ] **Step 1: Write failing tests for dismissal**

Append to `tests/theater/test_overlay.py`:
```python
from PySide6.QtTest import QTest


class TestTheaterOverlayDismissal:
    """Escape key, backdrop click, collapse button."""

    def _activate_and_wait(self, overlay, proxy, qtbot):
        """Helper: activate and wait for animation to finish."""
        overlay.activate(proxy)
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

    def test_escape_key_deactivates(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        QTest.keyClick(overlay, Qt.Key.Key_Escape)
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert not overlay.isVisible()
        assert proxy.currentWidget() is target

    def test_collapse_button_deactivates(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        overlay._collapse_btn.click()
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert not overlay.isVisible()
        assert proxy.currentWidget() is target

    def test_backdrop_click_deactivates(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        # Click at (1, 1) — inside the margin, outside the widget
        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert not overlay.isVisible()
        assert proxy.currentWidget() is target

    def test_click_inside_widget_does_not_deactivate(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        # Click in center — should be inside the expanded widget
        center = QPoint(parent_widget.width() // 2, parent_widget.height() // 2)
        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=center)

        assert overlay.isVisible()
        assert overlay._active_proxy is proxy
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/test_overlay.py::TestTheaterOverlayDismissal -v`

Expected: All 4 tests PASS (dismissal is already implemented in overlay.py).

- [ ] **Step 3: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add tests/theater/test_overlay.py
git commit -m "test(theater): dismissal trigger tests — Escape, backdrop click, collapse button"
```

---

## Task 5: TheaterOverlay resize tracking

**Files:**
- Modify: `tests/theater/test_overlay.py`

- [ ] **Step 1: Write failing tests for resize tracking**

Append to `tests/theater/test_overlay.py`:
```python
class TestTheaterOverlayResize:
    """Overlay and widget resize when parent resizes."""

    def _activate_and_wait(self, overlay, proxy, qtbot):
        overlay.activate(proxy)
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

    def test_overlay_resizes_with_parent(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        parent_widget.resize(1024, 768)
        QApplication.processEvents()

        assert overlay.width() == 1024
        assert overlay.height() == 768

    def test_widget_fills_new_area_on_resize(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        self._activate_and_wait(overlay, proxy, qtbot)

        parent_widget.resize(1024, 768)
        QApplication.processEvents()

        margin = TheaterOverlay._MARGIN
        expected_width = 1024 - 2 * margin
        expected_height = 768 - 2 * margin
        assert target.width() == expected_width
        assert target.height() == expected_height
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/test_overlay.py::TestTheaterOverlayResize -v`

Expected: All 2 tests PASS (resize tracking is already implemented in overlay.py eventFilter).

- [ ] **Step 3: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add tests/theater/test_overlay.py
git commit -m "test(theater): overlay resize tracking tests"
```

---

## Task 6: TheaterOverlay animation verification

**Files:**
- Modify: `tests/theater/test_overlay.py`

- [ ] **Step 1: Write tests that verify animation behavior**

Append to `tests/theater/test_overlay.py`:
```python
from PySide6.QtCore import QRect


class TestTheaterOverlayAnimation:
    """Animation setup and final states."""

    def test_activate_starts_animation(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        overlay.activate(proxy)

        assert overlay._anim_group is not None
        assert overlay._is_animating

    def test_backdrop_reaches_target_opacity_after_activate(
        self, parent_widget, qtbot
    ):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        overlay.activate(proxy)
        qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert overlay.backdrop_opacity == 150

    def test_widget_reaches_expanded_rect_after_activate(
        self, parent_widget, qtbot
    ):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)
        overlay.activate(proxy)
        qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        expected = overlay._expanded_rect()
        assert target.geometry() == expected

    def test_backdrop_reaches_zero_after_deactivate(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        overlay.activate(proxy)
        qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        overlay.deactivate()
        qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert overlay.backdrop_opacity == 0

    def test_is_animating_false_after_complete_cycle(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)

        overlay = TheaterOverlay(parent_widget)
        qtbot.addWidget(overlay)

        overlay.activate(proxy)
        qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)
        assert not overlay._is_animating

        overlay.deactivate()
        qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)
        assert not overlay._is_animating
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/test_overlay.py::TestTheaterOverlayAnimation -v`

Expected: All 5 tests PASS.

- [ ] **Step 3: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add tests/theater/test_overlay.py
git commit -m "test(theater): animation behavior verification tests"
```

---

## Task 7: TheaterManager

**Files:**
- Modify: `src/lightfall/ui/theater/manager.py`
- Create: `tests/theater/test_manager.py`

- [ ] **Step 1: Write failing tests for TheaterManager**

`tests/theater/test_manager.py`:
```python
"""Tests for TheaterManager — install, uninstall, activate delegation."""

import pytest
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lightfall.ui.theater.manager import TheaterManager, theater_manager
from lightfall.ui.theater.proxy import TheaterProxy


class TestTheaterManagerRegister:
    """Registration and signal wiring."""

    def test_proxy_auto_registered(self, qtbot):
        target = QLabel("plot")
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        assert id(target) in theater_manager._proxies

    def test_unregister_removes_proxy(self, qtbot):
        target = QLabel("plot")
        qtbot.addWidget(target)
        proxy = TheaterProxy(target)
        qtbot.addWidget(proxy)

        theater_manager.unregister(proxy)
        assert id(target) not in theater_manager._proxies


class TestTheaterManagerInstall:
    """install() layout surgery."""

    def test_install_wraps_widget_in_proxy(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)

        proxy = theater_manager.install(target)

        assert isinstance(proxy, TheaterProxy)
        assert proxy.target_widget is target
        # Proxy should be in the layout where target was
        assert parent_widget.layout().indexOf(proxy) >= 0

    def test_install_preserves_layout_index(self, parent_widget, qtbot):
        before = QLabel("before")
        target = QLabel("target")
        after = QLabel("after")
        layout = parent_widget.layout()
        layout.addWidget(before)
        layout.addWidget(target)
        layout.addWidget(after)

        proxy = theater_manager.install(target)

        assert layout.indexOf(before) == 0
        assert layout.indexOf(proxy) == 1
        assert layout.indexOf(after) == 2

    def test_install_raises_without_parent(self, qtbot):
        target = QLabel("orphan")
        qtbot.addWidget(target)

        with pytest.raises(ValueError, match="without a parent"):
            theater_manager.install(target)

    def test_install_raises_without_layout(self, qtbot):
        parent = QWidget()
        qtbot.addWidget(parent)
        target = QLabel("child", parent)

        with pytest.raises(ValueError, match="no layout"):
            theater_manager.install(target)


class TestTheaterManagerUninstall:
    """uninstall() layout restoration."""

    def test_uninstall_restores_widget(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        theater_manager.install(target)

        theater_manager.uninstall(target)

        # Widget should be back in the layout, proxy gone
        assert parent_widget.layout().indexOf(target) >= 0
        assert id(target) not in theater_manager._proxies

    def test_uninstall_preserves_layout_index(self, parent_widget, qtbot):
        before = QLabel("before")
        target = QLabel("target")
        after = QLabel("after")
        layout = parent_widget.layout()
        layout.addWidget(before)
        layout.addWidget(target)
        layout.addWidget(after)

        theater_manager.install(target)
        theater_manager.uninstall(target)

        assert layout.indexOf(before) == 0
        assert layout.indexOf(target) == 1
        assert layout.indexOf(after) == 2

    def test_uninstall_noop_for_unknown_widget(self, qtbot):
        target = QLabel("unknown")
        qtbot.addWidget(target)
        theater_manager.uninstall(target)  # should not raise


class TestTheaterManagerActivate:
    """activate() creates overlay lazily and delegates."""

    def test_activate_creates_overlay(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)
        proxy.show()
        parent_widget.show()

        theater_manager.activate(proxy)

        assert theater_manager._overlay is not None
        assert theater_manager._overlay.isVisible()

    def test_deactivate_delegates(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)
        proxy.show()
        parent_widget.show()

        theater_manager.activate(proxy)
        overlay = theater_manager._overlay
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        theater_manager.deactivate()
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert not overlay.isVisible()

    def test_is_active_property(self, parent_widget, qtbot):
        target = QLabel("plot")
        parent_widget.layout().addWidget(target)
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)
        proxy.show()
        parent_widget.show()

        assert not theater_manager.is_active

        theater_manager.activate(proxy)
        overlay = theater_manager._overlay
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert theater_manager.is_active

        theater_manager.deactivate()
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert not theater_manager.is_active
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/test_manager.py -v`

Expected: FAIL — `install`, `uninstall`, `is_active` not yet implemented.

- [ ] **Step 3: Implement full TheaterManager**

Replace the stub `src/lightfall/ui/theater/manager.py` with the full implementation:

```python
"""TheaterManager — singleton coordinator for theater mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from lightfall.ui.theater.overlay import TheaterOverlay
    from lightfall.ui.theater.proxy import TheaterProxy


class TheaterManager:
    """Coordinates TheaterProxy instances and the TheaterOverlay."""

    def __init__(self) -> None:
        self._proxies: dict[int, TheaterProxy] = {}
        self._overlay: TheaterOverlay | None = None

    def register(self, proxy: TheaterProxy) -> None:
        """Register a proxy and connect its expand signal."""
        widget_id = id(proxy.target_widget)
        self._proxies[widget_id] = proxy
        proxy.expand_requested.connect(lambda: self.activate(proxy))

    def unregister(self, proxy: TheaterProxy) -> None:
        """Unregister a proxy."""
        widget_id = id(proxy.target_widget)
        self._proxies.pop(widget_id, None)
        try:
            proxy.expand_requested.disconnect()
        except RuntimeError:
            pass

    def install(self, widget: QWidget) -> TheaterProxy:
        """Wrap an already-laid-out widget in a TheaterProxy.

        Finds the widget's parent layout and replaces the widget
        at the same index with a new TheaterProxy.
        """
        from lightfall.ui.theater.proxy import TheaterProxy

        parent = widget.parentWidget()
        if parent is None:
            msg = "Cannot install theater mode on a widget without a parent"
            raise ValueError(msg)
        layout = parent.layout()
        if layout is None:
            msg = "Cannot install theater mode: parent widget has no layout"
            raise ValueError(msg)

        index = layout.indexOf(widget)
        if index < 0:
            msg = "Widget not found in parent's layout"
            raise ValueError(msg)

        proxy = TheaterProxy(widget)
        layout.insertWidget(index, proxy)
        return proxy

    def uninstall(self, widget: QWidget) -> None:
        """Remove theater mode from a widget, restoring it to its layout."""
        widget_id = id(widget)
        proxy = self._proxies.get(widget_id)
        if proxy is None:
            return

        # Deactivate if currently in theater mode
        if self._overlay is not None and self._overlay._active_proxy is proxy:
            self._overlay._finish_deactivate()

        # Restore widget to layout
        parent = proxy.parentWidget()
        layout = parent.layout() if parent else None

        if layout is not None:
            index = layout.indexOf(proxy)
            target = proxy.take_widget()
            layout.removeWidget(proxy)
            if index >= 0:
                layout.insertWidget(index, target)
            else:
                layout.addWidget(target)

        self.unregister(proxy)
        proxy.deleteLater()

    def activate(self, proxy: TheaterProxy) -> None:
        """Expand a proxy's widget onto the overlay."""
        if self._overlay is None:
            from lightfall.ui.theater.overlay import TheaterOverlay

            parent = proxy.window()
            self._overlay = TheaterOverlay(parent)
        self._overlay.activate(proxy)

    def deactivate(self) -> None:
        """Collapse the currently expanded widget."""
        if self._overlay is not None:
            self._overlay.deactivate()

    @property
    def is_active(self) -> bool:
        """Whether a widget is currently in theater mode."""
        return (
            self._overlay is not None
            and self._overlay._active_proxy is not None
        )


theater_manager = TheaterManager()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/test_manager.py -v`

Expected: All 11 tests PASS.

- [ ] **Step 5: Run all theater tests**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/ -v`

Expected: All tests PASS (proxy: 12, overlay: 18, manager: 11).

- [ ] **Step 6: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lightfall/ui/theater/manager.py tests/theater/test_manager.py
git commit -m "feat(theater): TheaterManager — install, uninstall, activate delegation"
```

---

## Task 8: Package exports and integration test

**Files:**
- Modify: `src/lightfall/ui/theater/__init__.py`
- Modify: `tests/theater/test_manager.py`

- [ ] **Step 1: Write integration test**

Append to `tests/theater/test_manager.py`:
```python
class TestTheaterIntegration:
    """Full round-trip: install → expand → dismiss → verify."""

    def test_full_cycle_via_install(self, parent_widget, qtbot):
        """Simulates the intended usage: install, click expand, press Escape."""
        target = QLabel("My Plot")
        parent_widget.layout().addWidget(target)

        # Install theater mode
        proxy = theater_manager.install(target)
        proxy.show()
        parent_widget.show()

        # Verify proxy is in layout
        assert parent_widget.layout().indexOf(proxy) >= 0

        # Simulate expand button click
        theater_manager.activate(proxy)
        overlay = theater_manager._overlay
        assert overlay is not None
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        # Widget is on overlay
        assert target.parentWidget() is overlay
        assert theater_manager.is_active

        # Dismiss
        theater_manager.deactivate()
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        # Widget is back in proxy
        assert proxy.currentWidget() is target
        assert not theater_manager.is_active
        assert not overlay.isVisible()

    def test_full_cycle_via_direct_proxy(self, parent_widget, qtbot):
        """Direct TheaterProxy construction (no install)."""
        target = QLabel("My Image")
        proxy = TheaterProxy(target)
        parent_widget.layout().addWidget(proxy)
        proxy.show()
        parent_widget.show()

        # Activate via manager (proxy auto-registered)
        theater_manager.activate(proxy)
        overlay = theater_manager._overlay
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert target.parentWidget() is overlay

        # Deactivate
        theater_manager.deactivate()
        if overlay._anim_group is not None:
            qtbot.waitSignal(overlay._anim_group.finished, timeout=2000)

        assert proxy.currentWidget() is target
```

- [ ] **Step 2: Update package exports**

`src/lightfall/ui/theater/__init__.py`:
```python
"""Theater mode — generic widget expansion overlay.

Usage with install (existing widget in a layout)::

    from lightfall.ui.theater import theater_manager

    plot = pg.PlotWidget()
    layout.addWidget(plot)
    theater_manager.install(plot)

Usage with direct proxy construction::

    from lightfall.ui.theater import TheaterProxy

    proxy = TheaterProxy(my_image_view)
    layout.addWidget(proxy)
"""

from lightfall.ui.theater.manager import TheaterManager, theater_manager
from lightfall.ui.theater.overlay import TheaterOverlay
from lightfall.ui.theater.proxy import TheaterProxy

__all__ = [
    "TheaterManager",
    "TheaterOverlay",
    "TheaterProxy",
    "theater_manager",
]
```

- [ ] **Step 3: Run all theater tests**

Run: `cd ~/PycharmProjects/ncs/ncs && python -m pytest tests/theater/ -v`

Expected: All tests PASS.

- [ ] **Step 4: Verify imports work from the package**

Run: `cd ~/PycharmProjects/ncs/ncs && python -c "from lightfall.ui.theater import TheaterProxy, TheaterOverlay, TheaterManager, theater_manager; print('OK')"  `

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lightfall/ui/theater/__init__.py tests/theater/test_manager.py
git commit -m "feat(theater): package exports and integration tests"
```
