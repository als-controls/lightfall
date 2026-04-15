"""RunEngine control widget for state management.

Provides a compact GUI for inspecting and managing the RunEngine state,
including status display, control buttons, and queue information.
"""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPainter, QPixmap, qAlpha, qRed, qGreen, qBlue, qRgba
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from lucid.acquire.engine import Engine


SPINNING_STATES = frozenset({"running", "stopping"})
PAUSED_STATES = frozenset({"paused", "pausing"})
ERROR_STATES = frozenset({"aborting", "panicked"})

_LOGO_SIZE = 24
_FLASH_DURATION_MS = 1500
_SPIN_INTERVAL_MS = 33  # ~30 fps
_SPIN_DEGREES_PER_TICK = 12  # 30 fps * 12 deg = 360 deg/sec


def _bake_gray_pixmap(source: QImage) -> QPixmap:
    """Build a grayscale variant of source, preserving alpha.

    Pure Format_Grayscale8 conversion zeroes alpha. Doing it manually keeps
    soft edges anti-aliased against any background.
    """
    out = QImage(source.size(), QImage.Format.Format_ARGB32)
    out.fill(0)
    for y in range(source.height()):
        for x in range(source.width()):
            px = source.pixel(x, y)
            a = qAlpha(px)
            if a == 0:
                continue
            r, g, b = qRed(px), qGreen(px), qBlue(px)
            lum = (r * 299 + g * 587 + b * 114) // 1000
            out.setPixel(x, y, qRgba(lum, lum, lum, a))
    return QPixmap.fromImage(out)


def _bake_red_pixmap(source: QImage) -> QPixmap:
    """Build a red-tinted variant: red channel = source luminance, G=B=0.

    Preserves the logo's internal contrast (bright parts stay bright red,
    dark parts stay dark red). Alpha is preserved for soft edges.
    """
    out = QImage(source.size(), QImage.Format.Format_ARGB32)
    out.fill(0)
    for y in range(source.height()):
        for x in range(source.width()):
            px = source.pixel(x, y)
            a = qAlpha(px)
            if a == 0:
                continue
            r, g, b = qRed(px), qGreen(px), qBlue(px)
            lum = (r * 299 + g * 587 + b * 114) // 1000
            out.setPixel(x, y, qRgba(lum, 0, 0, a))
    return QPixmap.fromImage(out)


def _load_logo_pixmaps() -> tuple[QPixmap, QPixmap, QPixmap]:
    """Load logo.png and bake (color, gray, red) 24x24 pixmaps.

    Returns:
        Tuple of (color, gray, red) QPixmaps, all 24x24.
    """
    resource = files("lucid.ui.resources") / "logo.png"
    data = resource.read_bytes()
    raw = QImage.fromData(data, "PNG")
    if raw.isNull():
        raise RuntimeError("Failed to decode logo.png from lucid.ui.resources")
    scaled = raw.scaled(
        _LOGO_SIZE,
        _LOGO_SIZE,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QImage.Format.Format_ARGB32)
    color = QPixmap.fromImage(scaled)
    gray = _bake_gray_pixmap(scaled)
    red = _bake_red_pixmap(scaled)
    return color, gray, red


class SpinnerIndicator(QWidget):
    """Spinning ALS logo indicating RunEngine status.

    States:
    - idle / no engine: gray, static
    - running / stopping: color, spinning at 360 deg/sec
    - paused / pausing: color at 50% opacity, static
    - aborting / panicked: red, static (also triggers a 1500 ms flash)
    - error flash (transient): red, static, overrides state for 1500 ms

    The three color variants are pre-baked once at construction. A 30 fps
    timer drives rotation; it only runs while in a spinning state to avoid
    redraw churn when idle.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the spinner indicator."""
        super().__init__(parent)
        self.setFixedSize(_LOGO_SIZE, _LOGO_SIZE)

        self._color_pixmap, self._gray_pixmap, self._red_pixmap = _load_logo_pixmaps()
        self._status = "idle"
        self._rotation = 0.0
        self._flash_active = False

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(_SPIN_INTERVAL_MS)
        self._spin_timer.timeout.connect(self._on_spin_tick)

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(_FLASH_DURATION_MS)
        self._flash_timer.timeout.connect(self._on_flash_timeout)

    def set_status(self, status: str) -> None:
        """Update the displayed status.

        Starts/stops the spin timer based on whether the new status spins.
        """
        self._status = status.lower()
        if self._status in SPINNING_STATES:
            if not self._spin_timer.isActive():
                self._spin_timer.start()
        else:
            if self._spin_timer.isActive():
                self._spin_timer.stop()
        self.update()

    def flash_error(self) -> None:
        """Flash red for 1500 ms. Re-entrant: restarts the timer if called again."""
        self._flash_active = True
        self._flash_timer.start()  # restarts if already running
        self.update()

    @Slot()
    def _on_spin_tick(self) -> None:
        self._rotation = (self._rotation + _SPIN_DEGREES_PER_TICK) % 360
        self.update()

    @Slot()
    def _on_flash_timeout(self) -> None:
        self._flash_active = False
        self.update()

    def _pixmap_for_state(self) -> QPixmap:
        if self._status in ERROR_STATES:
            return self._red_pixmap
        if self._status in (PAUSED_STATES | SPINNING_STATES):
            return self._color_pixmap
        return self._gray_pixmap

    def paintEvent(self, event) -> None:
        """Render the (possibly rotated, possibly dimmed) pixmap."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self._flash_active:
            pixmap = self._red_pixmap
            opacity = 1.0
        else:
            pixmap = self._pixmap_for_state()
            opacity = 0.5 if self._status in PAUSED_STATES else 1.0

        painter.setOpacity(opacity)
        painter.translate(_LOGO_SIZE / 2, _LOGO_SIZE / 2)
        painter.rotate(self._rotation)
        painter.translate(-_LOGO_SIZE / 2, -_LOGO_SIZE / 2)
        painter.drawPixmap(0, 0, pixmap)


class RunEngineControlWidget(QWidget):
    """Compact widget for engine state management.

    Displays the current state and provides control buttons for
    pausing, resuming, aborting, and stopping the engine.

    Features:
    - Status indicator (colored dot + text)
    - Control buttons: Pause, Resume, Abort, Stop
    - Queue count display
    - Compact horizontal layout for toolbar embedding

    Signals:
        pause_requested: User clicked pause.
        resume_requested: User clicked resume.
        abort_requested: User clicked abort.
        stop_requested: User clicked stop.

    Example:
        >>> from lucid.acquire import get_engine
        >>> engine = get_engine()
        >>> control = RunEngineControlWidget()
        >>> control.set_engine(engine)
        >>> toolbar.addWidget(control)
    """

    pause_requested = Signal()
    resume_requested = Signal()
    abort_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the control widget.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._engine: Engine | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        # Status section
        status_frame = QFrame()
        status_frame.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(6, 2, 6, 2)
        status_layout.setSpacing(4)

        self._status_indicator = SpinnerIndicator()
        status_layout.addWidget(self._status_indicator)

        self._status_label = QLabel("Idle")
        self._status_label.setMinimumWidth(60)
        status_layout.addWidget(self._status_label)

        layout.addWidget(status_frame)

        # Pause/Resume toggle button
        self._pause_resume_btn = QPushButton("Pause")
        self._pause_resume_btn.setToolTip("Pause the current run at the next checkpoint")
        self._pause_resume_btn.setEnabled(False)
        self._pause_resume_btn.clicked.connect(self._on_pause_resume_clicked)
        layout.addWidget(self._pause_resume_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setToolTip("Stop the current run gracefully")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self._stop_btn)

        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setToolTip("Abort the current run immediately")
        self._abort_btn.setEnabled(False)
        self._abort_btn.clicked.connect(self._on_abort_clicked)
        layout.addWidget(self._abort_btn)

        # Queue info
        self._queue_label = QLabel("Queue: 0")
        self._queue_label.setToolTip("Number of plans queued")
        layout.addWidget(self._queue_label)

        layout.addStretch()

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_engine(self, engine: Engine) -> None:
        """Connect to an Engine instance.

        Args:
            engine: The Engine to control.
        """
        if self._engine is not None:
            self._disconnect_signals()

        self._engine = engine
        self._connect_signals()
        self._update_state()

    def set_run_engine(self, re: Engine) -> None:
        """Connect to an Engine instance.

        Deprecated: Use set_engine() instead.

        Args:
            re: The Engine to control.
        """
        self.set_engine(re)

    def _connect_signals(self) -> None:
        """Connect to engine signals."""
        if self._engine is None:
            return

        self._engine.sigStateChanged.connect(self._on_state_changed)
        self._engine.sigStart.connect(self._on_run_start)
        self._engine.sigFinish.connect(self._on_run_finish)
        self._engine.sigPause.connect(self._on_pause)
        self._engine.sigResume.connect(self._on_resume)
        self._engine.sigQueueChanged.connect(self._on_queue_changed)
        self._engine.sigException.connect(self._on_exception)

    def _disconnect_signals(self) -> None:
        """Disconnect from engine signals."""
        if self._engine is None:
            return

        try:
            self._engine.sigStateChanged.disconnect(self._on_state_changed)
            self._engine.sigStart.disconnect(self._on_run_start)
            self._engine.sigFinish.disconnect(self._on_run_finish)
            self._engine.sigPause.disconnect(self._on_pause)
            self._engine.sigResume.disconnect(self._on_resume)
            self._engine.sigQueueChanged.disconnect(self._on_queue_changed)
            self._engine.sigException.disconnect(self._on_exception)
        except RuntimeError:
            pass  # Already disconnected

    def _update_state(self) -> None:
        """Update UI to reflect current engine state."""
        if self._engine is None:
            self._status_indicator.set_status("idle")
            self._status_label.setText("No Engine")
            self._disable_all_buttons()
            return

        state = self._engine.state_name
        self._status_indicator.set_status(state)
        self._status_label.setText(state.capitalize())

        # Update button states
        is_running = state == "running"
        is_paused = state == "paused"

        # Update pause/resume toggle
        if is_paused:
            self._pause_resume_btn.setText("Resume")
            self._pause_resume_btn.setToolTip("Resume a paused run")
            self._pause_resume_btn.setEnabled(True)
        elif is_running:
            self._pause_resume_btn.setText("Pause")
            self._pause_resume_btn.setToolTip("Pause the current run at the next checkpoint")
            self._pause_resume_btn.setEnabled(True)
        else:
            self._pause_resume_btn.setText("Pause")
            self._pause_resume_btn.setToolTip("Pause the current run at the next checkpoint")
            self._pause_resume_btn.setEnabled(False)

        self._stop_btn.setEnabled(is_running or is_paused)
        self._abort_btn.setEnabled(is_running or is_paused)

        # Update queue count
        queue_size = self._engine.queue_size
        self._queue_label.setText(f"Queue: {queue_size}")

    def _disable_all_buttons(self) -> None:
        """Disable all control buttons."""
        self._pause_resume_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._abort_btn.setEnabled(False)

    # === Slots ===

    @Slot(str)
    def _on_state_changed(self, state: str) -> None:
        """Handle RunEngine state change.

        Args:
            state: New state string.
        """
        self._update_state()
        if state.lower() in ERROR_STATES:
            self._status_indicator.flash_error()
        logger.debug(f"RunEngine state changed: {state}")

    @Slot()
    def _on_run_start(self) -> None:
        """Handle run start."""
        self._update_state()

    @Slot()
    def _on_run_finish(self) -> None:
        """Handle run finish."""
        self._update_state()

    @Slot()
    def _on_pause(self) -> None:
        """Handle pause event."""
        self._update_state()

    @Slot()
    def _on_resume(self) -> None:
        """Handle resume event."""
        self._update_state()

    @Slot()
    def _on_queue_changed(self) -> None:
        """Handle queue change (plan submitted or dequeued)."""
        self._update_state()

    @Slot(Exception)
    def _on_exception(self, ex: Exception) -> None:
        """Flash the indicator red when the engine raises an exception."""
        self._status_indicator.flash_error()

    @Slot()
    def _on_pause_resume_clicked(self) -> None:
        """Handle pause/resume toggle button click."""
        if self._engine is None:
            return

        if self._engine.state_name == "paused":
            self._engine.resume()
            self.resume_requested.emit()
        else:
            self._engine.pause()
            self.pause_requested.emit()

    @Slot()
    def _on_stop_clicked(self) -> None:
        """Handle stop button click."""
        if self._engine is not None:
            self._engine.stop()
        self.stop_requested.emit()

    @Slot()
    def _on_abort_clicked(self) -> None:
        """Handle abort button click."""
        if self._engine is not None:
            self._engine.abort()
        self.abort_requested.emit()


class RunEngineStatusBar(QWidget):
    """Extended status bar variant with more details.

    Shows additional information like current plan name,
    elapsed time, and estimated completion.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the status bar.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._engine: Engine | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(12)

        # Embed the control widget
        self._control = RunEngineControlWidget()
        layout.addWidget(self._control)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # Plan info
        plan_layout = QVBoxLayout()
        plan_layout.setSpacing(0)

        self._plan_label = QLabel("Plan: --")
        self._plan_label.setStyleSheet("font-weight: bold;")
        plan_layout.addWidget(self._plan_label)

        self._progress_label = QLabel("Progress: --")
        plan_layout.addWidget(self._progress_label)

        layout.addLayout(plan_layout)
        layout.addStretch()

    def set_engine(self, engine: Engine) -> None:
        """Connect to an Engine instance.

        Args:
            engine: The Engine to control.
        """
        self._engine = engine
        self._control.set_engine(engine)

        # Connect additional signals for plan info
        engine.sigStart.connect(self._on_run_start)
        engine.sigFinish.connect(self._on_run_finish)
        engine.sigOutput.connect(self._on_document)

    def set_run_engine(self, re: Engine) -> None:
        """Connect to an Engine instance.

        Deprecated: Use set_engine() instead.

        Args:
            re: The Engine to control.
        """
        self.set_engine(re)

    @Slot()
    def _on_run_start(self) -> None:
        """Handle run start."""
        self._plan_label.setText("Plan: Running...")
        self._progress_label.setText("Progress: 0%")

    @Slot()
    def _on_run_finish(self) -> None:
        """Handle run finish."""
        self._plan_label.setText("Plan: --")
        self._progress_label.setText("Progress: --")

    @Slot(str, dict)
    def _on_document(self, name: str, doc: dict) -> None:
        """Handle document from run.

        Args:
            name: Document type.
            doc: Document data.
        """
        if name == "start":
            plan_name = doc.get("plan_name", "unknown")
            self._plan_label.setText(f"Plan: {plan_name}")
        elif name == "event":
            seq_num = doc.get("seq_num", 0)
            # We don't know total, so just show count
            self._progress_label.setText(f"Progress: {seq_num} events")
