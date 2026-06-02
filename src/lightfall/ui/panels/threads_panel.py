"""Threads panel for monitoring and managing background threads.

Provides visibility into running background threads managed by ThreadManager,
with management actions and introspection capabilities.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSplitter,
    QTableView,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from lucid.ui.models.thread_model import (
    ThreadFilterProxyModel,
    ThreadManagerObserver,
    ThreadRecord,
    ThreadStatus,
    ThreadTableModel,
)
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.utils.logging import logger
from lucid.utils.threads import thread_manager

if TYPE_CHECKING:
    pass


class ThreadDetailsWidget(QWidget):
    """Widget showing detailed information about a selected thread."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the details widget."""
        super().__init__(parent)
        self._current_record: ThreadRecord | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Thread info group
        info_group = QGroupBox("Thread Information")
        info_layout = QVBoxLayout(info_group)

        # Basic info labels
        info_grid = QHBoxLayout()

        left_col = QVBoxLayout()
        self._name_label = QLabel("-")
        self._name_label.setStyleSheet("font-weight: bold;")
        left_col.addWidget(QLabel("Name:"))
        left_col.addWidget(self._name_label)

        self._key_label = QLabel("-")
        left_col.addWidget(QLabel("Key:"))
        left_col.addWidget(self._key_label)

        self._status_label = QLabel("-")
        left_col.addWidget(QLabel("Status:"))
        left_col.addWidget(self._status_label)

        info_grid.addLayout(left_col)

        right_col = QVBoxLayout()
        self._method_label = QLabel("-")
        right_col.addWidget(QLabel("Method:"))
        right_col.addWidget(self._method_label)

        self._started_label = QLabel("-")
        right_col.addWidget(QLabel("Started:"))
        right_col.addWidget(self._started_label)

        self._duration_label = QLabel("-")
        right_col.addWidget(QLabel("Duration:"))
        right_col.addWidget(self._duration_label)

        info_grid.addLayout(right_col)

        # Third column for CPU info
        cpu_col = QVBoxLayout()
        self._cpu_label = QLabel("-")
        cpu_col.addWidget(QLabel("CPU:"))
        cpu_col.addWidget(self._cpu_label)

        self._native_id_label = QLabel("-")
        cpu_col.addWidget(QLabel("Thread ID:"))
        cpu_col.addWidget(self._native_id_label)

        cpu_col.addStretch()
        info_grid.addLayout(cpu_col)

        info_layout.addLayout(info_grid)

        layout.addWidget(info_group)

        # Arguments group
        args_group = QGroupBox("Arguments")
        args_layout = QVBoxLayout(args_group)
        self._args_text = QLabel("-")
        self._args_text.setWordWrap(True)
        args_layout.addWidget(self._args_text)
        layout.addWidget(args_group)

        # Exception group (only visible when there's an error)
        self._exception_group = QGroupBox("Exception Details")
        exception_layout = QVBoxLayout(self._exception_group)
        self._exception_text = QTextEdit()
        self._exception_text.setReadOnly(True)
        self._exception_text.setMaximumHeight(150)
        exception_layout.addWidget(self._exception_text)
        layout.addWidget(self._exception_group)
        self._exception_group.setVisible(False)

        layout.addStretch()

    def set_record(self, record: ThreadRecord | None) -> None:
        """Set the thread record to display.

        Args:
            record: Thread record to display or None to clear.
        """
        self._current_record = record

        if record is None:
            self._clear()
            return

        self._name_label.setText(record.name)
        self._key_label.setText(record.key or "-")
        self._status_label.setText(record.status.value.title())
        self._method_label.setText(record.method_name or "-")
        self._started_label.setText(record.started_at.strftime("%Y-%m-%d %H:%M:%S"))
        self._duration_label.setText(record.format_duration())
        self._args_text.setText(record.args_repr or "-")

        # CPU info
        self._cpu_label.setText(record.format_cpu())
        self._native_id_label.setText(str(record.native_thread_id) if record.native_thread_id else "-")

        # CPU color based on usage
        if record.status == ThreadStatus.RUNNING:
            if record.cpu_percent > 80:
                self._cpu_label.setStyleSheet("color: #F44336; font-weight: bold;")
            elif record.cpu_percent > 50:
                self._cpu_label.setStyleSheet("color: #FF9800; font-weight: bold;")
            elif record.cpu_percent > 10:
                self._cpu_label.setStyleSheet("color: #2196F3;")
            else:
                self._cpu_label.setStyleSheet("")
        else:
            self._cpu_label.setStyleSheet("")

        # Status color
        status_styles = {
            ThreadStatus.RUNNING: "color: #2196F3; font-weight: bold;",
            ThreadStatus.COMPLETED: "color: #4CAF50;",
            ThreadStatus.CANCELLED: "color: #FF9800;",
            ThreadStatus.ERROR: "color: #F44336; font-weight: bold;",
            ThreadStatus.PENDING: "color: #9E9E9E;",
        }
        self._status_label.setStyleSheet(status_styles.get(record.status, ""))

        # Show exception if present
        if record.exception_msg:
            self._exception_group.setVisible(True)
            self._exception_text.setText(record.exception_msg)
        else:
            self._exception_group.setVisible(False)

    def refresh(self) -> None:
        """Refresh the display with current record data."""
        if self._current_record is not None:
            self._duration_label.setText(self._current_record.format_duration())
            self._cpu_label.setText(self._current_record.format_cpu())

    def _clear(self) -> None:
        """Clear all fields."""
        self._name_label.setText("-")
        self._key_label.setText("-")
        self._status_label.setText("-")
        self._status_label.setStyleSheet("")
        self._method_label.setText("-")
        self._started_label.setText("-")
        self._duration_label.setText("-")
        self._cpu_label.setText("-")
        self._cpu_label.setStyleSheet("")
        self._native_id_label.setText("-")
        self._args_text.setText("-")
        self._exception_group.setVisible(False)


class ThreadsPanel(BasePanel):
    """Panel for monitoring and managing background threads.

    ThreadsPanel provides:
    - Real-time view of ThreadManager threads
    - Thread status monitoring (running/completed/cancelled/error)
    - Management actions (cancel, cancel all, clear history)
    - Introspection of thread method/arguments
    - Historical thread tracking

    This panel uses Qt Model/View architecture with ThreadTableModel.
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.threads",
        name="Threads",
        description="Monitor and manage background threads",
        icon="spider-thread",
        category="Developer",
        required_permission=None,
        singleton=True,
        closable=True,
        keywords=["thread", "background", "task", "worker", "async", "concurrent"],
        # Docking preferences - bottom sidebar
        default_area="bottom",
        sidebar_group="bottom",
        auto_hide=True,
        sidebar_order=4,
    )

    # Signals
    thread_selected = Signal(object)  # ThreadRecord

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the threads panel."""
        # Create observer and model before calling super().__init__
        self._observer = ThreadManagerObserver(poll_interval_ms=100)
        self._model = ThreadTableModel(self._observer)
        self._proxy_model = ThreadFilterProxyModel()
        self._proxy_model.setSourceModel(self._model)

        super().__init__(parent)

        # Start observing after UI is set up
        self._observer.start()

    def _setup_ui(self) -> None:
        """Setup the panel UI."""
        # Main splitter
        splitter = QSplitter(Qt.Orientation.Vertical)
        self._layout.addWidget(splitter)

        # Top section: table and controls
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = self._create_toolbar()
        top_layout.addWidget(toolbar)

        # Filter row
        filter_layout = QHBoxLayout()

        # Search box
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search by name or key...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search_changed)
        filter_layout.addWidget(self._search_input, stretch=1)

        # Status filter
        filter_layout.addWidget(QLabel("Status:"))
        self._status_filter = QComboBox()
        self._status_filter.addItem("All", None)
        self._status_filter.addItem("Running", {ThreadStatus.RUNNING})
        self._status_filter.addItem("Completed", {ThreadStatus.COMPLETED})
        self._status_filter.addItem("Cancelled", {ThreadStatus.CANCELLED})
        self._status_filter.addItem("Error", {ThreadStatus.ERROR})
        self._status_filter.addItem("Active", {ThreadStatus.PENDING, ThreadStatus.RUNNING})
        self._status_filter.currentIndexChanged.connect(self._on_status_filter_changed)
        filter_layout.addWidget(self._status_filter)

        # Auto-refresh toggle
        self._auto_refresh_check = QCheckBox("Auto-refresh")
        self._auto_refresh_check.setChecked(True)
        self._auto_refresh_check.setToolTip("Automatically refresh thread statuses")
        self._auto_refresh_check.toggled.connect(self._on_auto_refresh_toggled)
        filter_layout.addWidget(self._auto_refresh_check)

        top_layout.addLayout(filter_layout)

        # Table view
        self._table_view = QTableView()
        self._table_view.setModel(self._proxy_model)
        self._table_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table_view.setAlternatingRowColors(True)
        self._table_view.setSortingEnabled(True)
        self._table_view.sortByColumn(4, Qt.SortOrder.DescendingOrder)  # Sort by started, newest first

        # Configure header (columns: Name, Key, Status, CPU, Started, Duration)
        header = self._table_view.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Name
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Key
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Status
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # CPU
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Started
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)  # Duration

        top_layout.addWidget(self._table_view)
        splitter.addWidget(top_widget)

        # Bottom section: details
        self._details_widget = ThreadDetailsWidget()
        splitter.addWidget(self._details_widget)

        # Set splitter sizes
        splitter.setSizes([300, 150])

        # Connect selection
        self._table_view.selectionModel().selectionChanged.connect(self._on_selection_changed)

        # Details refresh timer
        self._details_timer = QTimer(self)
        self._details_timer.timeout.connect(self._details_widget.refresh)
        self._details_timer.start(500)

    def _create_toolbar(self) -> QToolBar:
        """Create the panel toolbar."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        # Refresh action
        refresh_action = QAction("Refresh", self)
        refresh_action.setToolTip("Force refresh thread states")
        refresh_action.triggered.connect(self._refresh)
        toolbar.addAction(refresh_action)

        toolbar.addSeparator()

        # Cancel selected
        self._cancel_action = QAction("Cancel", self)
        self._cancel_action.setToolTip("Cancel selected running thread")
        self._cancel_action.setEnabled(False)
        self._cancel_action.triggered.connect(self._cancel_selected)
        toolbar.addAction(self._cancel_action)

        # Cancel all
        cancel_all_action = QAction("Cancel All", self)
        cancel_all_action.setToolTip("Cancel all running threads")
        cancel_all_action.triggered.connect(self._cancel_all)
        toolbar.addAction(cancel_all_action)

        toolbar.addSeparator()

        # Clear history
        clear_action = QAction("Clear History", self)
        clear_action.setToolTip("Remove non-running threads from view")
        clear_action.triggered.connect(self._clear_history)
        toolbar.addAction(clear_action)

        return toolbar

    def _refresh(self) -> None:
        """Force refresh thread states."""
        self._observer._poll()
        logger.debug("Thread panel refreshed")

    def _cancel_selected(self) -> None:
        """Cancel the selected running thread."""
        record = self._get_selected_record()
        if record is None:
            return

        thread = record.get_thread()
        if thread is not None and thread.isRunning():
            logger.info("Cancelling thread: {}", record.name)
            thread.cancel()
            self._refresh()

    def _cancel_all(self) -> None:
        """Cancel all running threads."""
        logger.info("Cancelling all running threads")
        thread_manager.cancel_all()
        self._refresh()

    def _clear_history(self) -> None:
        """Clear non-running threads from history."""
        self._observer.clear_history()
        logger.debug("Thread history cleared")

    def _get_selected_record(self) -> ThreadRecord | None:
        """Get the currently selected thread record."""
        selection = self._table_view.selectionModel().selectedIndexes()
        if not selection:
            return None

        proxy_index = selection[0]
        source_index = self._proxy_model.mapToSource(proxy_index)
        return self._model.get_record(source_index.row())

    # === Signal Handlers ===

    @Slot(str)
    def _on_search_changed(self, text: str) -> None:
        """Handle search text change."""
        self._proxy_model.setFilterRegularExpression(text)

    @Slot(int)
    def _on_status_filter_changed(self, index: int) -> None:
        """Handle status filter change."""
        statuses = self._status_filter.currentData()
        self._proxy_model.set_visible_statuses(statuses)

    @Slot(bool)
    def _on_auto_refresh_toggled(self, checked: bool) -> None:
        """Handle auto-refresh toggle."""
        if checked:
            self._observer.start()
            self._details_timer.start(500)
        else:
            self._observer.stop()
            self._details_timer.stop()

    @Slot()
    def _on_selection_changed(self) -> None:
        """Handle table selection change."""
        record = self._get_selected_record()
        self._details_widget.set_record(record)

        # Update cancel action state
        can_cancel = (
            record is not None
            and record.status == ThreadStatus.RUNNING
            and record.get_thread() is not None
        )
        self._cancel_action.setEnabled(can_cancel)

        if record is not None:
            self.thread_selected.emit(record)

    # === Lifecycle ===

    def _on_closing(self) -> None:
        """Handle panel closing."""
        self._observer.stop()
        self._details_timer.stop()

    # === Introspection ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get thread panel-specific introspection data."""
        records = self._observer.get_records()

        running_count = sum(1 for r in records if r.status == ThreadStatus.RUNNING)
        completed_count = sum(1 for r in records if r.status == ThreadStatus.COMPLETED)
        error_count = sum(1 for r in records if r.status == ThreadStatus.ERROR)
        cancelled_count = sum(1 for r in records if r.status == ThreadStatus.CANCELLED)

        selected = self._get_selected_record()
        selected_data = None
        if selected is not None:
            selected_data = {
                "name": selected.name,
                "key": selected.key,
                "status": selected.status.value,
                "method": selected.method_name,
                "duration": selected.format_duration(),
                "cpu_percent": selected.cpu_percent if selected.status == ThreadStatus.RUNNING else None,
                "has_error": selected.exception_msg is not None,
            }

        # Calculate total CPU usage of running threads
        total_cpu = sum(r.cpu_percent for r in records if r.status == ThreadStatus.RUNNING)

        return {
            "total_threads": len(records),
            "running_count": running_count,
            "completed_count": completed_count,
            "error_count": error_count,
            "cancelled_count": cancelled_count,
            "total_cpu_percent": total_cpu,
            "search_text": self._search_input.text(),
            "status_filter": self._status_filter.currentText(),
            "auto_refresh": self._auto_refresh_check.isChecked(),
            "selected_thread": selected_data,
            "threads": [
                {
                    "name": r.name,
                    "key": r.key,
                    "status": r.status.value,
                    "duration": r.format_duration(),
                    "cpu_percent": r.cpu_percent if r.status == ThreadStatus.RUNNING else None,
                }
                for r in records[:20]  # Limit to first 20
            ],
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for this panel."""
        actions = super()._get_available_actions()
        actions.extend([
            {
                "name": "refresh",
                "description": "Force refresh thread states",
                "method": "action_refresh",
            },
            {
                "name": "cancel_selected",
                "description": "Cancel the selected running thread",
                "method": "action_cancel_selected",
            },
            {
                "name": "cancel_all",
                "description": "Cancel all running threads",
                "method": "action_cancel_all",
            },
            {
                "name": "clear_history",
                "description": "Remove non-running threads from view",
                "method": "action_clear_history",
            },
            {
                "name": "search",
                "description": "Search threads by name or key",
                "method": "action_search",
                "parameters": {"query": "string"},
            },
            {
                "name": "filter_by_status",
                "description": "Filter threads by status",
                "method": "action_filter_by_status",
                "parameters": {"status": "running|completed|cancelled|error|all"},
            },
            {
                "name": "cancel_by_key",
                "description": "Cancel a thread by its key",
                "method": "action_cancel_by_key",
                "parameters": {"key": "string"},
            },
        ])
        return actions

    def action_refresh(self) -> bool:
        """Action: Force refresh thread states."""
        self._refresh()
        return True

    def action_cancel_selected(self) -> bool:
        """Action: Cancel the selected running thread."""
        record = self._get_selected_record()
        if record is None:
            return False
        self._cancel_selected()
        return True

    def action_cancel_all(self) -> bool:
        """Action: Cancel all running threads."""
        self._cancel_all()
        return True

    def action_clear_history(self) -> bool:
        """Action: Remove non-running threads from view."""
        self._clear_history()
        return True

    def action_search(self, query: str) -> bool:
        """Action: Search threads by name or key.

        Args:
            query: Search query string.

        Returns:
            True if search was performed.
        """
        self._search_input.setText(query)
        return True

    def action_filter_by_status(self, status: str) -> bool:
        """Action: Filter threads by status.

        Args:
            status: Status to filter by (running, completed, cancelled, error, all).

        Returns:
            True if filter was applied.
        """
        status_map = {
            "all": 0,
            "running": 1,
            "completed": 2,
            "cancelled": 3,
            "error": 4,
            "active": 5,
        }
        index = status_map.get(status.lower(), 0)
        self._status_filter.setCurrentIndex(index)
        return True

    def action_cancel_by_key(self, key: str) -> bool:
        """Action: Cancel a thread by its key.

        Args:
            key: Thread key to cancel.

        Returns:
            True if thread was found and cancelled.
        """
        return thread_manager.cancel(key)
