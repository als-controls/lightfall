"""Queue management panel for Bluesky RunEngine.

Provides a comprehensive interface for managing the engine's procedure queue
with drag-and-drop reordering, execution history tracking, and live progress.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.widgets.plan_edit_dialog import PlanEditDialog
from lucid.ui.widgets.queue_view import (
    QueueModel,
    QueueTableView,
    RecentItem,
    RecentModel,
    RecentStatus,
    RecentTableView,
)
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.acquire.engine.base import BaseEngine, PrioritizedProcedure


class RunningHeaderWidget(QFrame):
    """Widget showing the currently running procedure with live progress.

    Displays the plan name, elapsed time, and optional progress indicators.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the header widget.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._procedure: PrioritizedProcedure | None = None
        self._start_time: datetime | None = None
        self._current_point: int = 0
        self._total_points: int = 0

        # Timer for elapsed time updates (must be created before _setup_ui)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_elapsed)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the widget UI."""
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            RunningHeaderWidget {
                background-color: palette(mid);
                border-radius: 4px;
                padding: 4px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Top row: icon, name, priority
        top_row = QHBoxLayout()

        self._status_icon = QLabel("\u25B6")  # Play symbol
        self._status_icon.setStyleSheet("font-size: 14pt; color: #4CAF50;")
        top_row.addWidget(self._status_icon)

        self._name_label = QLabel("Idle")
        self._name_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        top_row.addWidget(self._name_label)

        top_row.addStretch()

        self._priority_label = QLabel("")
        top_row.addWidget(self._priority_label)

        layout.addLayout(top_row)

        # Bottom row: elapsed, progress, points
        bottom_row = QHBoxLayout()

        self._elapsed_label = QLabel("")
        bottom_row.addWidget(self._elapsed_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumHeight(12)
        self._progress_bar.setMinimum(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.hide()
        bottom_row.addWidget(self._progress_bar, 1)

        self._points_label = QLabel("")
        bottom_row.addWidget(self._points_label)

        layout.addLayout(bottom_row)

        self._set_idle_state()

    def _set_idle_state(self) -> None:
        """Set the widget to idle state."""
        self._status_icon.setText("\u23F8")  # Pause symbol
        self._status_icon.setStyleSheet("font-size: 14pt; color: palette(text);")
        self._name_label.setText("Idle")
        self._priority_label.setText("")
        self._elapsed_label.setText("")
        self._points_label.setText("")
        self._progress_bar.hide()
        self._timer.stop()

    def set_procedure(self, procedure: PrioritizedProcedure) -> None:
        """Set the currently running procedure.

        Args:
            procedure: The procedure that started.
        """
        self._procedure = procedure
        self._start_time = datetime.now()
        self._current_point = 0
        self._total_points = 0

        self._status_icon.setText("\u25B6")  # Play symbol
        self._status_icon.setStyleSheet("font-size: 14pt; color: #4CAF50;")
        self._name_label.setText(procedure.name or "procedure")
        self._priority_label.setText(f"Priority: {procedure.priority}")
        self._elapsed_label.setText("Elapsed: 00:00")
        self._points_label.setText("")
        self._progress_bar.hide()

        self._timer.start(1000)  # Update every second

    def clear_procedure(self) -> None:
        """Clear the current procedure (execution finished)."""
        self._procedure = None
        self._start_time = None
        self._set_idle_state()

    def update_progress(self, current: int, total: int) -> None:
        """Update progress from event documents.

        Args:
            current: Current point number.
            total: Total points (0 if unknown).
        """
        self._current_point = current
        self._total_points = total

        if total > 0:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(current)
            self._progress_bar.show()
            self._points_label.setText(f"Point {current}/{total}")
        else:
            self._progress_bar.hide()
            self._points_label.setText(f"Point {current}")

    @Slot()
    def _update_elapsed(self) -> None:
        """Update elapsed time display."""
        if self._start_time is None:
            return

        elapsed = datetime.now() - self._start_time
        total_seconds = int(elapsed.total_seconds())
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        self._elapsed_label.setText(f"Elapsed: {minutes:02d}:{seconds:02d}")


class QueuePanel(BasePanel):
    """Panel for managing the Bluesky RunEngine queue.

    Provides:
    - Live view of currently running procedure with progress
    - Queue tab: View, reorder, edit, remove pending procedures
    - Recent tab: View completed/failed/cancelled procedures with retry option

    Signals:
        plan_queued(str): Emitted when a plan is added to queue.
        plan_started(str): Emitted when a plan starts executing.
        plan_finished(str, str): Emitted when a plan finishes (name, status).
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.queue",
        name="Queue",
        description="Manage the RunEngine queue and view execution history",
        icon="human-queue",
        category="Acquisition",
        singleton=True,
        closable=True,
        keywords=["queue", "pending", "history", "recent", "runengine", "bluesky"],
        # Docking preferences - bottom sidebar
        default_area="bottom",
        sidebar_group="bottom",
        auto_hide=True,
        sidebar_order=3,
    )

    plan_queued = Signal(str)  # plan name
    plan_started = Signal(str)  # plan name
    plan_finished = Signal(str, str)  # plan name, status

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the queue panel.

        Args:
            parent: Parent widget.
        """
        self._engine: BaseEngine | None = None
        self._current_start_time: datetime | None = None
        self._num_points: int = 0
        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Set up the panel UI."""
        # Running header
        self._running_header = RunningHeaderWidget()
        self._layout.addWidget(self._running_header)

        # Tab widget
        self._tabs = QTabWidget()
        self._layout.addWidget(self._tabs, 1)

        # Queue tab
        queue_widget = QWidget()
        queue_layout = QVBoxLayout(queue_widget)
        queue_layout.setContentsMargins(0, 4, 0, 0)

        self._queue_model = QueueModel()
        self._queue_view = QueueTableView()
        self._queue_view.setModel(self._queue_model)

        # Connect queue view signals
        self._queue_model.reorder_requested.connect(self._on_reorder_requested)
        self._queue_view.edit_requested.connect(self._on_edit_requested)
        self._queue_view.remove_requested.connect(self._on_remove_requested)
        self._queue_view.duplicate_requested.connect(self._on_duplicate_requested)

        queue_layout.addWidget(self._queue_view)

        # Queue footer
        queue_footer = QHBoxLayout()
        self._pending_label = QLabel("0 pending")
        queue_footer.addWidget(self._pending_label)
        queue_footer.addStretch()

        self._clear_queue_btn = QPushButton("Clear Queue")
        self._clear_queue_btn.setToolTip("Remove all pending procedures")
        self._clear_queue_btn.clicked.connect(self._on_clear_queue)
        queue_footer.addWidget(self._clear_queue_btn)

        queue_layout.addLayout(queue_footer)

        self._tabs.addTab(queue_widget, "Queue")

        # Recent tab
        recent_widget = QWidget()
        recent_layout = QVBoxLayout(recent_widget)
        recent_layout.setContentsMargins(0, 4, 0, 0)

        # Filter checkboxes
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Show:"))

        self._completed_check = QCheckBox("Completed")
        self._completed_check.setChecked(True)
        self._completed_check.toggled.connect(self._update_recent_filter)
        filter_layout.addWidget(self._completed_check)

        self._failed_check = QCheckBox("Failed")
        self._failed_check.setChecked(True)
        self._failed_check.toggled.connect(self._update_recent_filter)
        filter_layout.addWidget(self._failed_check)

        self._cancelled_check = QCheckBox("Cancelled")
        self._cancelled_check.setChecked(True)
        self._cancelled_check.toggled.connect(self._update_recent_filter)
        filter_layout.addWidget(self._cancelled_check)

        filter_layout.addStretch()
        recent_layout.addLayout(filter_layout)

        self._recent_model = RecentModel()
        self._recent_view = RecentTableView()
        self._recent_view.setModel(self._recent_model)

        # Connect recent view signals
        self._recent_view.retry_requested.connect(self._on_retry_requested)
        self._recent_view.duplicate_requested.connect(self._on_recent_duplicate_requested)

        recent_layout.addWidget(self._recent_view)

        # Recent footer
        recent_footer = QHBoxLayout()
        self._recent_count_label = QLabel("0 items")
        recent_footer.addWidget(self._recent_count_label)
        recent_footer.addStretch()

        self._clear_recent_btn = QPushButton("Clear History")
        self._clear_recent_btn.clicked.connect(self._on_clear_recent)
        recent_footer.addWidget(self._clear_recent_btn)

        recent_layout.addLayout(recent_footer)

        self._tabs.addTab(recent_widget, "Recent")

        # Auto-configure with engine
        self._auto_configure()

    def _auto_configure(self) -> None:
        """Auto-configure with the singleton engine."""
        try:
            from lucid.acquire import get_engine

            engine = get_engine()
            self.set_engine(engine)
        except Exception as e:
            logger.debug(f"Could not auto-configure engine: {e}")

    def set_engine(self, engine: BaseEngine) -> None:
        """Connect to an engine instance.

        Args:
            engine: The engine to manage.
        """
        if self._engine is not None:
            try:
                self._engine.sigQueueChanged.disconnect(self._on_queue_changed)
                self._engine.sigStart.disconnect(self._on_run_start)
                self._engine.sigFinish.disconnect(self._on_run_finish)
                self._engine.sigAbort.disconnect(self._on_run_abort)
                self._engine.sigException.disconnect(self._on_run_exception)
                self._engine.sigOutput.disconnect(self._on_document)
            except RuntimeError:
                pass

        self._engine = engine
        self._queue_model.set_engine(engine)

        # Connect signals
        engine.sigQueueChanged.connect(self._on_queue_changed)
        engine.sigStart.connect(self._on_run_start)
        engine.sigFinish.connect(self._on_run_finish)
        engine.sigAbort.connect(self._on_run_abort)
        engine.sigException.connect(self._on_run_exception)
        engine.sigOutput.connect(self._on_document)

        self._update_pending_count()
        logger.info("QueuePanel connected to engine")

    # === Introspection API ===

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for Claude MCP tools."""
        data = {
            "panel_id": self.panel_metadata.id,
            "panel_name": self.panel_metadata.name,
            "has_engine": self._engine is not None,
        }

        if self._engine is not None:
            data["engine_state"] = self._engine.state_name
            data["queue_size"] = self._engine.queue_size

            current = self._engine.get_current_procedure()
            if current is not None:
                data["current_procedure"] = {
                    "id": current.id,
                    "name": current.name,
                    "priority": current.priority,
                }
            else:
                data["current_procedure"] = None

            queue_items = self._engine.get_queue_items()
            data["pending_procedures"] = [
                {
                    "id": item.id,
                    "name": item.name,
                    "priority": item.priority,
                    "submitted_at": item.submitted_at.isoformat(),
                }
                for item in queue_items
            ]

        data["recent_count"] = self._recent_model.rowCount()

        return data

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for this panel."""
        return [
            {
                "name": "clear_queue",
                "description": "Remove all pending procedures",
                "method": "action_clear_queue",
            },
            {
                "name": "remove_from_queue",
                "description": "Remove a specific procedure by ID",
                "method": "action_remove_from_queue",
                "params": {"procedure_id": "string"},
            },
            {
                "name": "update_priority",
                "description": "Change priority of a queued procedure",
                "method": "action_update_priority",
                "params": {"procedure_id": "string", "new_priority": "int"},
            },
        ]

    def action_clear_queue(self) -> int:
        """Clear all pending procedures.

        Returns:
            Number of procedures removed.
        """
        if self._engine is None:
            return 0
        return self._engine.clear_queue()

    def action_remove_from_queue(self, procedure_id: str) -> bool:
        """Remove a procedure from the queue.

        Args:
            procedure_id: The procedure ID.

        Returns:
            True if removed.
        """
        if self._engine is None:
            return False
        return self._engine.remove_from_queue(procedure_id)

    def action_update_priority(self, procedure_id: str, new_priority: int) -> bool:
        """Update a procedure's priority.

        Args:
            procedure_id: The procedure ID.
            new_priority: The new priority value.

        Returns:
            True if updated.
        """
        if self._engine is None:
            return False
        return self._engine.update_priority(procedure_id, new_priority)

    # === Slots ===

    @Slot()
    def _on_queue_changed(self) -> None:
        """Handle queue changed signal."""
        self._update_pending_count()

    def _update_pending_count(self) -> None:
        """Update the pending count label."""
        if self._engine is not None:
            count = self._engine.queue_size
            self._pending_label.setText(f"{count} pending")
        else:
            self._pending_label.setText("0 pending")

    @Slot()
    def _on_run_start(self) -> None:
        """Handle run start signal."""
        if self._engine is None:
            return

        self._current_start_time = datetime.now()
        self._num_points = 0

        current = self._engine.get_current_procedure()
        if current is not None:
            self._running_header.set_procedure(current)
            self.plan_started.emit(current.name)

    @Slot()
    def _on_run_finish(self) -> None:
        """Handle run finish signal."""
        self._add_to_recent(RecentStatus.COMPLETED)
        self._running_header.clear_procedure()

    @Slot()
    def _on_run_abort(self) -> None:
        """Handle run abort signal."""
        self._add_to_recent(RecentStatus.CANCELLED)
        self._running_header.clear_procedure()

    @Slot(Exception)
    def _on_run_exception(self, exception: Exception) -> None:
        """Handle run exception signal."""
        self._add_to_recent(RecentStatus.FAILED, str(exception))
        self._running_header.clear_procedure()

    def _add_to_recent(self, status: RecentStatus, error: str = "") -> None:
        """Add the current procedure to recent history.

        Args:
            status: The completion status.
            error: Error message if failed.
        """
        if self._engine is None:
            return

        # Get info about the just-completed procedure from the header
        # since _current_procedure may have been cleared already
        if self._current_start_time is not None:
            duration = (datetime.now() - self._current_start_time).total_seconds()
        else:
            duration = 0.0

        # Try to get from running header's procedure reference
        procedure = self._running_header._procedure
        if procedure is not None:
            item = RecentItem(
                procedure_id=procedure.id,
                name=procedure.name,
                kwargs=dict(procedure.kwargs),
                status=status,
                duration_seconds=duration,
                completed_at=datetime.now(),
                error=error,
            )
            self._recent_model.add_item(item)
            self._update_recent_count()
            self.plan_finished.emit(procedure.name, status.value)

        self._current_start_time = None

    @Slot(str, dict)
    def _on_document(self, name: str, doc: dict) -> None:
        """Handle document from engine."""
        if name == "event":
            seq_num = doc.get("seq_num", 0)
            # Try to get total from descriptor
            total = doc.get("num_points", 0)
            self._running_header.update_progress(seq_num, total)
            self._num_points = seq_num
        elif name == "start":
            # Get num_points from start document if available
            num_points = doc.get("num_points", 0)
            if num_points > 0:
                self._running_header.update_progress(0, num_points)

    @Slot(str, int)
    def _on_reorder_requested(self, procedure_id: str, target_position: int) -> None:
        """Handle reorder request from drag-drop.

        Args:
            procedure_id: ID of the procedure being moved.
            target_position: Target position in queue.
        """
        if self._engine is None:
            return

        # Calculate new priority based on target position
        # We'll set priority to be between the items at target-1 and target
        items = self._engine.get_queue_items()

        if target_position <= 0:
            # Moving to first position - make priority lower than first item
            if items:
                new_priority = items[0].priority - 1
            else:
                new_priority = 0
        elif target_position >= len(items):
            # Moving to last position - make priority higher than last item
            if items:
                new_priority = items[-1].priority + 1
            else:
                new_priority = 1
        else:
            # Moving to middle - average of neighbors
            prev_priority = items[target_position - 1].priority
            next_priority = items[target_position].priority
            new_priority = (prev_priority + next_priority) // 2
            # If neighbors have same priority, just use that
            if new_priority == prev_priority:
                new_priority = prev_priority

        self._engine.update_priority(procedure_id, new_priority)

    @Slot(str)
    def _on_edit_requested(self, procedure_id: str) -> None:
        """Handle edit request for a queue item.

        Args:
            procedure_id: ID of the procedure to edit.
        """
        if self._engine is None:
            return

        procedure = self._engine.get_procedure_by_id(procedure_id)
        if procedure is None:
            logger.warning(f"Procedure {procedure_id[:8]} not found")
            return

        dialog = PlanEditDialog(procedure, self)
        dialog.changes_saved.connect(self._on_edit_changes_saved)
        dialog.exec()

    @Slot(str, int, dict)
    def _on_edit_changes_saved(
        self, procedure_id: str, new_priority: int, new_kwargs: dict
    ) -> None:
        """Handle saved changes from edit dialog.

        Args:
            procedure_id: The procedure ID.
            new_priority: New priority value.
            new_kwargs: New parameter values.
        """
        if self._engine is None:
            return

        # Update priority
        self._engine.update_priority(procedure_id, new_priority)

        # Note: Updating kwargs requires modifying the procedure object
        # which is a bit tricky with the current design. For now, we only
        # support priority changes. Full kwargs editing would require
        # removing and re-adding the procedure.

        from lucid.ui.toast import ToastManager

        ToastManager.get_instance().success("Changes Saved", "Priority updated")

    @Slot(str)
    def _on_remove_requested(self, procedure_id: str) -> None:
        """Handle remove request for a queue item.

        Args:
            procedure_id: ID of the procedure to remove.
        """
        if self._engine is None:
            return

        if self._engine.remove_from_queue(procedure_id):
            from lucid.ui.toast import ToastManager

            ToastManager.get_instance().info("Removed", "Procedure removed from queue")

    @Slot(str)
    def _on_duplicate_requested(self, procedure_id: str) -> None:
        """Handle duplicate request for a queue item.

        Args:
            procedure_id: ID of the procedure to duplicate.
        """
        if self._engine is None:
            return

        procedure = self._engine.get_procedure_by_id(procedure_id)
        if procedure is None:
            return

        # Submit a copy with same parameters
        # Note: This requires access to the original procedure generator,
        # which may not be easily re-creatable. For now, show a message.
        from lucid.ui.toast import ToastManager

        ToastManager.get_instance().info(
            "Duplicate",
            "To duplicate, use the Bluesky panel to submit with same parameters",
        )

    @Slot()
    def _on_clear_queue(self) -> None:
        """Handle clear queue button click."""
        if self._engine is None:
            return

        count = self._engine.clear_queue()
        if count > 0:
            from lucid.ui.toast import ToastManager

            ToastManager.get_instance().info(
                "Queue Cleared", f"Removed {count} procedure(s)"
            )

    @Slot()
    def _update_recent_filter(self) -> None:
        """Update the recent model filter based on checkboxes."""
        statuses = set()
        if self._completed_check.isChecked():
            statuses.add(RecentStatus.COMPLETED)
        if self._failed_check.isChecked():
            statuses.add(RecentStatus.FAILED)
        if self._cancelled_check.isChecked():
            statuses.add(RecentStatus.CANCELLED)

        self._recent_model.set_status_filter(statuses)
        self._update_recent_count()

    def _update_recent_count(self) -> None:
        """Update the recent count label."""
        count = self._recent_model.rowCount()
        self._recent_count_label.setText(f"{count} items")

    @Slot(object)
    def _on_retry_requested(self, item: RecentItem) -> None:
        """Handle retry request for a failed item.

        Args:
            item: The recent item to retry.
        """
        from lucid.ui.toast import ToastManager

        # Retrying requires recreating the plan generator, which isn't
        # straightforward. Show guidance instead.
        ToastManager.get_instance().info(
            "Retry",
            f"To retry '{item.name}', use the Bluesky panel with the same parameters",
        )

    @Slot(object)
    def _on_recent_duplicate_requested(self, item: RecentItem) -> None:
        """Handle duplicate request from recent tab.

        Args:
            item: The recent item to duplicate.
        """
        from lucid.ui.toast import ToastManager

        ToastManager.get_instance().info(
            "Add to Queue",
            f"To re-run '{item.name}', use the Bluesky panel with the same parameters",
        )

    @Slot()
    def _on_clear_recent(self) -> None:
        """Handle clear recent button click."""
        self._recent_model.clear()
        self._update_recent_count()
