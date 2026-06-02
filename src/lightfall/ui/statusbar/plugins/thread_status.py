"""Thread status plugin for NCS status bar.

Displays the number of background threads reporting progress,
device-level move progress from the RunEngine waiting hook,
and scan-level progress from document events.

Clicking the button opens an overlay with per-thread progress bars.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lucid.ui.theme import ThemeManager
from lucid.utils.logging import logger
from lucid.utils.threads import QThreadFuture, thread_manager


class _ProgressOverlay(QFrame):
    """Popup overlay listing per-thread, per-device, and scan progress bars."""

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

        # Thread rows: thread id -> widgets
        self._rows: dict[int, QWidget] = {}
        self._bars: dict[int, QProgressBar] = {}
        self._labels: dict[int, QLabel] = {}

        # Device rows: device name -> widgets
        self._device_rows: dict[str, QWidget] = {}
        self._device_bars: dict[str, QProgressBar] = {}
        self._device_labels: dict[str, QLabel] = {}

        # Separator between thread and device sections
        self._separator: QFrame | None = None

        # Pending device-removal timers (so clear_devices can cancel them)
        self._device_removal_timers: dict[str, QTimer] = {}

        # Scan row (always at top when present)
        self._scan_row: QWidget | None = None
        self._scan_bar: QProgressBar | None = None
        self._scan_label: QLabel | None = None
        self._scan_removal_timer: QTimer | None = None

    # ------------------------------------------------------------------
    # Thread rows
    # ------------------------------------------------------------------

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
        self._update_separator()

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
        self._update_separator()

    # ------------------------------------------------------------------
    # Device rows
    # ------------------------------------------------------------------

    def upsert_device(
        self, name: str, current: float, initial: float, target: float, fraction: float
    ) -> None:
        """Create or update a device progress row.

        If *fraction* >= 0, sets bar range 0-100 and value to int(fraction * 100).
        If *fraction* < 0 (indeterminate), sets bar min=0, max=0 (pulsing).
        """
        if name not in self._device_rows:
            self._add_device_row(name)
        bar = self._device_bars[name]
        if fraction < 0:
            bar.setMinimum(0)
            bar.setMaximum(0)  # pulsing / indeterminate
        else:
            bar.setMinimum(0)
            bar.setMaximum(100)
            bar.setValue(int(fraction * 100))

    def mark_device_done(self, name: str) -> None:
        """Set device bar to 100% and schedule removal after 1 second."""
        if name not in self._device_bars:
            return
        bar = self._device_bars[name]
        bar.setMinimum(0)
        bar.setMaximum(100)
        bar.setValue(100)

        old_timer = self._device_removal_timers.pop(name, None)
        if old_timer is not None:
            old_timer.stop()

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda n=name: self._remove_device(n))
        self._device_removal_timers[name] = timer
        timer.start(1000)

    def clear_devices(self) -> None:
        """Remove all device rows immediately, cancelling pending timers."""
        for timer in self._device_removal_timers.values():
            timer.stop()
        self._device_removal_timers.clear()

        for name in list(self._device_rows):
            self._remove_device(name)

    def _add_device_row(self, name: str) -> None:
        row = QWidget()
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(2)

        label = QLabel(name)
        subdued = self._subdued_text_color()
        label.setStyleSheet(f"font-size: 11px; color: {subdued};")
        row_layout.addWidget(label)

        bar = QProgressBar()
        bar.setTextVisible(True)
        bar.setFixedHeight(14)
        row_layout.addWidget(bar)

        self._device_rows[name] = row
        self._device_bars[name] = bar
        self._device_labels[name] = label
        self._list_layout.insertWidget(self._list_layout.count() - 1, row)
        self._update_separator()

    def _remove_device(self, name: str) -> None:
        """Remove a single device row."""
        self._device_removal_timers.pop(name, None)
        row = self._device_rows.pop(name, None)
        self._device_bars.pop(name, None)
        self._device_labels.pop(name, None)
        if row is not None:
            self._list_layout.removeWidget(row)
            row.deleteLater()
        self._update_separator()

    # ------------------------------------------------------------------
    # Scan row
    # ------------------------------------------------------------------

    def cancel_scan_removal(self) -> None:
        """Cancel any pending scan removal timer."""
        if self._scan_removal_timer is not None:
            self._scan_removal_timer.stop()
            self._scan_removal_timer = None

    def upsert_scan(self, event_count: int, num_points: int | None) -> None:
        """Create or update the scan progress row at the top of the overlay."""
        if self._scan_row is None:
            self._add_scan_row()

        if self._scan_bar is None or self._scan_label is None:
            return

        if num_points is not None and num_points > 0:
            self._scan_bar.setMinimum(0)
            self._scan_bar.setMaximum(100)
            pct = min(int(event_count / num_points * 100), 100)
            self._scan_bar.setValue(pct)
            self._scan_label.setText(f"Scan ({event_count}/{num_points})")
        else:
            self._scan_bar.setMinimum(0)
            self._scan_bar.setMaximum(0)
            self._scan_label.setText(f"Scan ({event_count} pts)")

    def mark_scan_done(self) -> None:
        """Set scan bar to 100% and schedule removal after 1 second."""
        if self._scan_bar is None:
            return
        self._scan_bar.setMinimum(0)
        self._scan_bar.setMaximum(100)
        self._scan_bar.setValue(100)
        if self._scan_label is not None:
            self._scan_label.setText("Scan (complete)")

        if self._scan_removal_timer is not None:
            self._scan_removal_timer.stop()

        self._scan_removal_timer = QTimer(self)
        self._scan_removal_timer.setSingleShot(True)
        self._scan_removal_timer.timeout.connect(self._remove_scan_row)
        self._scan_removal_timer.start(1000)

    def _add_scan_row(self) -> None:
        row = QWidget()
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(2)

        label = QLabel("Scan")
        label.setStyleSheet("font-size: 11px; font-weight: bold;")
        row_layout.addWidget(label)

        bar = QProgressBar()
        bar.setTextVisible(True)
        bar.setFixedHeight(16)
        row_layout.addWidget(bar)

        self._scan_row = row
        self._scan_bar = bar
        self._scan_label = label
        self._list_layout.insertWidget(0, row)

    def _remove_scan_row(self) -> None:
        if self._scan_row is not None:
            self._list_layout.removeWidget(self._scan_row)
            self._scan_row.deleteLater()
        self._scan_row = None
        self._scan_bar = None
        self._scan_label = None
        self._scan_removal_timer = None

    # ------------------------------------------------------------------
    # Theming helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _subdued_text_color() -> str:
        """Return a palette-derived subdued text color string for stylesheets."""
        app = QApplication.instance()
        if app is not None:
            palette = app.palette()
            return palette.color(QPalette.ColorRole.PlaceholderText).name()
        return "#888"

    # ------------------------------------------------------------------
    # Separator management
    # ------------------------------------------------------------------

    def _update_separator(self) -> None:
        """Show/hide a thin separator line between thread and device sections."""
        has_threads = len(self._rows) > 0
        has_devices = len(self._device_rows) > 0
        need_separator = has_threads and has_devices

        if need_separator and self._separator is None:
            self._separator = QFrame()
            self._separator.setFrameShape(QFrame.Shape.HLine)
            self._separator.setFixedHeight(1)
            mid_color = self._subdued_text_color()
            self._separator.setStyleSheet(f"color: {mid_color};")
            insert_idx = len(self._rows)
            if self._scan_row is not None:
                insert_idx += 1
            self._list_layout.insertWidget(insert_idx, self._separator)
        elif not need_separator and self._separator is not None:
            self._list_layout.removeWidget(self._separator)
            self._separator.deleteLater()
            self._separator = None

    # ------------------------------------------------------------------
    # Counts
    # ------------------------------------------------------------------

    @property
    def task_count(self) -> int:
        return len(self._rows)

    @property
    def device_count(self) -> int:
        return len(self._device_rows)

    @property
    def has_scan(self) -> bool:
        return self._scan_row is not None


class ThreadStatusPlugin(StatusBarPlugin):
    """Status bar plugin showing background thread activity.

    Renders as the default flat button; clicking opens an overlay with
    per-thread progress bars, per-device progress, and scan progress.
    Hides itself entirely when there is no activity.
    """

    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="lucid.statusbar.threads",
        name="Thread Status",
        description="Shows background task progress",
        priority=5,
        position="permanent",
        tooltip="Click to see background task progress",
    )

    def __init__(self) -> None:
        super().__init__()
        self._overlay: _ProgressOverlay | None = None
        self._theme_manager: ThemeManager | None = None
        self._tracked: set[int] = set()
        self._scanning: bool = False
        self._scan_uid: str | None = None
        self._scan_event_count: int = 0
        self._scan_num_points: int | None = None
        self._scan_plan_name: str | None = None

    @property
    def name(self) -> str:
        return "thread_status"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the default flat button, plus the lazy overlay."""
        widget = super().create_widget(parent)
        self._theme_manager = ThemeManager.get_instance()
        self._overlay = _ProgressOverlay()
        return widget

    def on_clicked(self) -> None:
        """Toggle the progress overlay anchored to the button."""
        if self._overlay is None or self._button is None:
            return
        if self._overlay.isVisible():
            self._overlay.hide()
            return

        pos = self._button.mapToGlobal(self._button.rect().topLeft())
        self._overlay.adjustSize()
        pos.setY(pos.y() - self._overlay.sizeHint().height())
        self._overlay.move(pos)
        self._overlay.show()

    def update(self) -> None:
        count = len(self._tracked)
        scanning = self._scanning

        if count == 0 and not scanning:
            self.set_text("")
            self.set_tooltip("")
            self.set_visible(False)
            return

        scan_label = self._scan_plan_name or "scanning"
        scan_tooltip = (
            f"Plan '{self._scan_plan_name}' running"
            if self._scan_plan_name
            else "Scan in progress"
        )

        if scanning and count == 0:
            self.set_text(f"⏳ {scan_label}")
            self.set_tooltip(scan_tooltip)
        elif scanning and count > 0:
            task_suffix = f"{count} task{'s' if count != 1 else ''}"
            self.set_text(f"⏳ {scan_label} + {task_suffix}")
            self.set_tooltip(f"{scan_tooltip}, {task_suffix}")
        else:
            self.set_text(f"⏳ {count} task{'s' if count != 1 else ''}")
            self.set_tooltip(f"{count} background task{'s' if count != 1 else ''} running")
        self.set_visible(True)

    def connect_signals(self) -> None:
        thread_manager.sigProgress.connect(self._on_progress)
        thread_manager.sigFinished.connect(self._on_finished)

        try:
            from lucid.acquire import get_engine

            engine = get_engine()
            if hasattr(engine, "waiting_bridge"):
                bridge = engine.waiting_bridge
                bridge.sigDeviceProgress.connect(self._on_device_progress)
                bridge.sigDeviceFinished.connect(self._on_device_finished)
                bridge.sigWaitGroupCleared.connect(self._on_wait_cleared)
            engine.sigOutput.connect(self._on_document)
        except Exception:
            logger.debug("Engine not available for ThreadStatusPlugin signal connection")

    def disconnect_signals(self) -> None:
        try:
            thread_manager.sigProgress.disconnect(self._on_progress)
        except RuntimeError:
            pass
        try:
            thread_manager.sigFinished.disconnect(self._on_finished)
        except RuntimeError:
            pass

        try:
            from lucid.acquire import get_engine

            engine = get_engine()
            if hasattr(engine, "waiting_bridge"):
                bridge = engine.waiting_bridge
                try:
                    bridge.sigDeviceProgress.disconnect(self._on_device_progress)
                except RuntimeError:
                    pass
                try:
                    bridge.sigDeviceFinished.disconnect(self._on_device_finished)
                except RuntimeError:
                    pass
                try:
                    bridge.sigWaitGroupCleared.disconnect(self._on_wait_cleared)
                except RuntimeError:
                    pass
            try:
                engine.sigOutput.disconnect(self._on_document)
            except RuntimeError:
                pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Thread callbacks
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Device callbacks (from WaitingHookBridge)
    # ------------------------------------------------------------------

    def _on_device_progress(
        self, name: str, current: float, initial: float, target: float, fraction: float
    ) -> None:
        if self._overlay is not None:
            self._overlay.upsert_device(name, current, initial, target, fraction)

    def _on_device_finished(self, name: str) -> None:
        if self._overlay is not None:
            self._overlay.mark_device_done(name)

    def _on_wait_cleared(self) -> None:
        if self._overlay is not None:
            self._overlay.clear_devices()

    # ------------------------------------------------------------------
    # Document callbacks (scan progress)
    # ------------------------------------------------------------------

    def _on_document(self, name: str, doc: dict) -> None:
        if self._overlay is None:
            return

        if name == "start":
            self._scanning = True
            self._scan_uid = doc.get("uid")
            self._scan_event_count = 0
            num_points = doc.get("num_points")
            if num_points is not None:
                try:
                    num_points = int(num_points)
                except (ValueError, TypeError):
                    num_points = None
            self._scan_num_points = num_points
            plan_name = doc.get("plan_name")
            self._scan_plan_name = plan_name if isinstance(plan_name, str) else None
            self._overlay.cancel_scan_removal()
            self._overlay.upsert_scan(0, num_points)
            self.update()

        elif name == "event":
            self._scan_event_count += 1
            self._overlay.upsert_scan(self._scan_event_count, self._scan_num_points)

        elif name == "stop":
            self._scanning = False
            self._overlay.mark_scan_done()
            self._scan_uid = None
            self._scan_event_count = 0
            self._scan_num_points = None
            self._scan_plan_name = None
            self.update()

    def get_introspection_data(self) -> dict[str, Any]:
        data = super().get_introspection_data()
        data["tracked_thread_count"] = len(self._tracked)
        data["scanning"] = self._scanning
        return data
