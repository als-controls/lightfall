"""Thread status plugin for NCS status bar.

Displays the number of background threads reporting progress.
Clicking the label opens an overlay with per-thread progress bars.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lucid.ui.theme import ThemeManager
from lucid.utils.threads import QThreadFuture, thread_manager

if TYPE_CHECKING:
    pass


class _ProgressOverlay(QFrame):
    """Popup overlay listing per-thread progress bars."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(320)
        self.setMaximumHeight(300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        header = QLabel("Background Tasks")
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch()
        scroll.setWidget(self._list_widget)

        # thread id -> row widget mapping
        self._rows: dict[int, QWidget] = {}
        self._bars: dict[int, QProgressBar] = {}
        self._labels: dict[int, QLabel] = {}

    def upsert(self, thread: QThreadFuture, current: float, minimum: float, maximum: float) -> None:
        """Add or update a progress row for a thread."""
        tid = id(thread)
        if tid not in self._rows:
            self._add_row(thread, tid)
        bar = self._bars[tid]
        bar.setMinimum(int(minimum))
        bar.setMaximum(int(maximum))
        bar.setValue(int(current))

    def remove(self, thread: QThreadFuture) -> None:
        """Remove a thread's progress row."""
        tid = id(thread)
        row = self._rows.pop(tid, None)
        self._bars.pop(tid, None)
        self._labels.pop(tid, None)
        if row is not None:
            self._list_layout.removeWidget(row)
            row.deleteLater()

    def _add_row(self, thread: QThreadFuture, tid: int) -> None:
        row = QWidget()
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(2)

        name = getattr(thread, "_name", None) or "Thread"
        label = QLabel(name)
        label.setStyleSheet("font-size: 11px;")
        row_layout.addWidget(label)

        bar = QProgressBar()
        bar.setTextVisible(True)
        bar.setFixedHeight(16)
        row_layout.addWidget(bar)

        self._rows[tid] = row
        self._bars[tid] = bar
        self._labels[tid] = label
        # Insert before the stretch at the end
        self._list_layout.insertWidget(self._list_layout.count() - 1, row)

    @property
    def task_count(self) -> int:
        return len(self._rows)


class _ClickableLabel(QLabel):
    """QLabel that emits a click via a callback."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_click: Any = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: Any) -> None:
        if self.on_click is not None:
            self.on_click()
        super().mousePressEvent(event)


class ThreadStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing background thread activity.

    Displays the count of threads that have reported progress.
    Clicking opens an overlay with per-thread progress bars.
    Threads appear when they first emit sigProgress and disappear
    when they finish (sigFinished from ThreadManager).
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lucid.statusbar.threads",
        name="Thread Status",
        description="Shows background task progress",
        priority=90,
        position="permanent",
        tooltip="Click to see background task progress",
    )

    def __init__(self) -> None:
        super().__init__()
        self._label: _ClickableLabel | None = None
        self._overlay: _ProgressOverlay | None = None
        self._theme_manager: ThemeManager | None = None
        # Track threads that have reported progress (even if overlay not open)
        self._tracked: set[int] = set()

    @property
    def name(self) -> str:
        return "thread_status"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        self._theme_manager = ThemeManager.get_instance()

        container = QWidget(parent)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = _ClickableLabel(container)
        self._label.on_click = self._toggle_overlay
        layout.addWidget(self._label)

        self._overlay = _ProgressOverlay()

        return container

    def update(self) -> None:
        if self._label is None:
            return
        count = len(self._tracked)
        if count == 0:
            self._label.setText("")
            self._label.setToolTip("")
            self._label.setVisible(False)
        else:
            self._label.setText(f"\u23f3 {count} task{'s' if count != 1 else ''}")
            self._label.setToolTip(f"{count} background task{'s' if count != 1 else ''} running")
            self._label.setVisible(True)

    def connect_signals(self) -> None:
        thread_manager.sigProgress.connect(self._on_progress)
        thread_manager.sigFinished.connect(self._on_finished)

    def disconnect_signals(self) -> None:
        try:
            thread_manager.sigProgress.disconnect(self._on_progress)
        except RuntimeError:
            pass
        try:
            thread_manager.sigFinished.disconnect(self._on_finished)
        except RuntimeError:
            pass

    def _on_progress(self, thread: QThreadFuture, current: float, minimum: float, maximum: float) -> None:
        self._tracked.add(id(thread))
        if self._overlay is not None:
            self._overlay.upsert(thread, current, minimum, maximum)
        self.update()

    def _on_finished(self, thread: QThreadFuture) -> None:
        self._tracked.discard(id(thread))
        if self._overlay is not None:
            self._overlay.remove(thread)
        self.update()

    def _toggle_overlay(self) -> None:
        if self._overlay is None or self._label is None:
            return
        if self._overlay.isVisible():
            self._overlay.hide()
        else:
            # Position above the label
            pos = self._label.mapToGlobal(self._label.rect().topLeft())
            self._overlay.adjustSize()
            pos.setY(pos.y() - self._overlay.sizeHint().height())
            self._overlay.move(pos)
            self._overlay.show()

    def get_introspection_data(self) -> dict[str, Any]:
        data = super().get_introspection_data()
        data["tracked_thread_count"] = len(self._tracked)
        return data
