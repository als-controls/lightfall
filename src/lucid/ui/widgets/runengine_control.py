"""RunEngine control widget for state management.

Provides a compact GUI for inspecting and managing the RunEngine state,
including status display, control buttons, and queue information.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter
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


class StatusIndicator(QWidget):
    """A colored dot indicating RunEngine status.

    Colors:
    - Gray: Idle
    - Green: Running
    - Yellow: Paused
    - Red: Error/Aborting
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the status indicator.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._color = QColor("#888888")  # Default gray
        self._status = "idle"

    def set_status(self, status: str) -> None:
        """Set the status and update color.

        Args:
            status: Status string (idle, running, paused, aborting).
        """
        self._status = status.lower()

        color_map = {
            "idle": "#888888",  # Gray
            "running": "#4CAF50",  # Green
            "paused": "#FFC107",  # Yellow/Amber
            "aborting": "#F44336",  # Red
            "stopping": "#FF9800",  # Orange
            "panicked": "#F44336",  # Red
        }

        self._color = QColor(color_map.get(self._status, "#888888"))
        self.update()

    def paintEvent(self, event) -> None:
        """Paint the status indicator."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw filled circle
        painter.setBrush(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, 12, 12)


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

        self._status_indicator = StatusIndicator()
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
