"""Queue management widgets for Bluesky RunEngine.

Provides models and views for managing the engine's procedure queue
with drag-and-drop reordering and execution history tracking.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QMimeData,
    QModelIndex,
    QPoint,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QTableView,
    QWidget,
)

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.acquire.engine.base import BaseEngine, PrioritizedProcedure


class QueueModel(QAbstractTableModel):
    """Table model for the engine's pending procedure queue.

    Displays queued procedures with columns for position, name, parameters,
    priority, and submission time. Supports drag-and-drop reordering.

    Signals:
        reorder_requested(str, int): Emitted when user drags to reorder.
            Args are (procedure_id, target_position).
    """

    COLUMNS = ["#", "Plan", "Parameters", "Priority", "Submitted"]
    MIME_TYPE = "application/x-ncs-queue-item"

    reorder_requested = Signal(str, int)  # procedure_id, target_position

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the queue model.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._items: list[PrioritizedProcedure] = []
        self._engine: BaseEngine | None = None

    def set_engine(self, engine: BaseEngine) -> None:
        """Connect to an engine instance.

        Args:
            engine: The engine whose queue to display.
        """
        if self._engine is not None:
            try:
                self._engine.sigQueueChanged.disconnect(self._on_queue_changed)
            except RuntimeError:
                pass

        self._engine = engine
        engine.sigQueueChanged.connect(self._on_queue_changed)
        self._refresh()

    def _on_queue_changed(self) -> None:
        """Handle queue changed signal from engine."""
        self._refresh()

    def _refresh(self) -> None:
        """Refresh items from engine."""
        self.beginResetModel()
        if self._engine is not None:
            self._items = self._engine.get_queue_items()
        else:
            self._items = []
        self.endResetModel()

    def get_item(self, row: int) -> PrioritizedProcedure | None:
        """Get the procedure at a specific row.

        Args:
            row: Row index.

        Returns:
            The procedure or None if out of bounds.
        """
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def get_item_by_id(self, procedure_id: str) -> PrioritizedProcedure | None:
        """Get a procedure by its ID.

        Args:
            procedure_id: The procedure's unique ID.

        Returns:
            The procedure or None if not found.
        """
        for item in self._items:
            if item.id == procedure_id:
                return item
        return None

    # === QAbstractTableModel implementation ===

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        """Get number of rows."""
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._items)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: ARG002
        """Get number of columns."""
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Get data for index."""
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if row >= len(self._items):
            return None

        item = self._items[row]

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:  # Position
                return str(row + 1)
            elif col == 1:  # Plan name
                return item.name or "procedure"
            elif col == 2:  # Parameters
                return self._format_kwargs(item.kwargs)
            elif col == 3:  # Priority
                return str(item.priority)
            elif col == 4:  # Submitted
                return item.submitted_at.strftime("%H:%M:%S")
        elif role == Qt.ItemDataRole.ToolTipRole:
            if col == 2:  # Full parameters on hover
                return json.dumps(item.kwargs, indent=2, default=str)
            elif col == 4:  # Full timestamp
                return item.submitted_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        elif role == Qt.ItemDataRole.UserRole:
            return item

        return None

    def _format_kwargs(self, kwargs: dict[str, Any]) -> str:
        """Format kwargs for display.

        Args:
            kwargs: Parameter dictionary.

        Returns:
            Formatted string.
        """
        if not kwargs:
            return ""
        parts = []
        for key, value in list(kwargs.items())[:3]:  # First 3 params
            if isinstance(value, (list, tuple)):
                value_str = f"[{len(value)} items]"
            elif isinstance(value, str) and len(value) > 20:
                value_str = value[:17] + "..."
            else:
                value_str = str(value)
            parts.append(f"{key}={value_str}")
        result = ", ".join(parts)
        if len(kwargs) > 3:
            result += f", +{len(kwargs) - 3} more"
        return result

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        """Get header data."""
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """Get item flags."""
        default_flags = super().flags(index)
        if not index.isValid():
            return default_flags | Qt.ItemFlag.ItemIsDropEnabled
        return (
            default_flags
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )

    # === Drag and Drop support ===

    def supportedDropActions(self) -> Qt.DropAction:
        """Return supported drop actions."""
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        """Return supported MIME types."""
        return [self.MIME_TYPE]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:
        """Create MIME data for drag operation."""
        mime_data = QMimeData()
        if indexes:
            row = indexes[0].row()
            if 0 <= row < len(self._items):
                item = self._items[row]
                mime_data.setData(self.MIME_TYPE, item.id.encode())
        return mime_data

    def dropMimeData(
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,  # noqa: ARG002
        parent: QModelIndex,
    ) -> bool:
        """Handle drop event."""
        if action == Qt.DropAction.IgnoreAction:
            return True

        if not data.hasFormat(self.MIME_TYPE):
            return False

        procedure_id = bytes(data.data(self.MIME_TYPE)).decode()

        # Determine target position
        if row != -1:
            target_row = row
        elif parent.isValid():
            target_row = parent.row()
        else:
            target_row = self.rowCount()

        # Emit signal for the panel to handle via engine
        self.reorder_requested.emit(procedure_id, target_row)
        return True


class QueueTableView(QTableView):
    """Table view for queue items with drag-drop and context menu.

    Signals:
        edit_requested(str): Emitted when user wants to edit an item.
        remove_requested(str): Emitted when user wants to remove an item.
        duplicate_requested(str): Emitted when user wants to duplicate an item.
    """

    edit_requested = Signal(str)  # procedure_id
    remove_requested = Signal(str)  # procedure_id
    duplicate_requested = Signal(str)  # procedure_id

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the view.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._setup_view()

    def _setup_view(self) -> None:
        """Configure the view settings."""
        # Enable drag and drop
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

        # Selection
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # Appearance
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)

        # Context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # Double-click to edit
        self.doubleClicked.connect(self._on_double_click)

    def setModel(self, model: QueueModel) -> None:
        """Set the model and configure columns.

        Args:
            model: The queue model.
        """
        super().setModel(model)

        # Configure column widths
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # #
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Plan
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # Params
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Priority
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Submitted

    def _show_context_menu(self, pos: QPoint) -> None:
        """Show context menu at position."""
        index = self.indexAt(pos)
        if not index.isValid():
            return

        item = index.data(Qt.ItemDataRole.UserRole)
        if item is None:
            return

        menu = QMenu(self)

        edit_action = menu.addAction("Edit...")
        edit_action.triggered.connect(lambda: self.edit_requested.emit(item.id))

        duplicate_action = menu.addAction("Duplicate")
        duplicate_action.triggered.connect(lambda: self.duplicate_requested.emit(item.id))

        menu.addSeparator()

        remove_action = menu.addAction("Remove")
        remove_action.triggered.connect(lambda: self.remove_requested.emit(item.id))

        menu.exec(self.viewport().mapToGlobal(pos))

    def _on_double_click(self, index: QModelIndex) -> None:
        """Handle double-click to edit."""
        item = index.data(Qt.ItemDataRole.UserRole)
        if item is not None:
            self.edit_requested.emit(item.id)


class RecentStatus(str, Enum):
    """Status of a completed procedure."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class RecentItem:
    """A completed procedure in the recent history.

    Attributes:
        procedure_id: Original procedure ID.
        name: Procedure/plan name.
        kwargs: Original parameters.
        status: How it finished.
        duration_seconds: Execution time.
        completed_at: When it finished.
        error: Error message if failed.
    """

    procedure_id: str
    name: str
    kwargs: dict[str, Any]
    status: RecentStatus
    duration_seconds: float
    completed_at: datetime
    error: str = ""


class RecentModel(QAbstractTableModel):
    """Table model for recently completed procedures.

    Maintains a FIFO list with a maximum of 100 items.
    Supports filtering by status.
    """

    COLUMNS = ["Plan", "Parameters", "Status", "Duration", "Completed"]
    MAX_ITEMS = 100

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the model.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._items: deque[RecentItem] = deque(maxlen=self.MAX_ITEMS)
        self._filtered_items: list[RecentItem] = []
        self._status_filter: set[RecentStatus] = set(RecentStatus)

    def add_item(self, item: RecentItem) -> None:
        """Add a completed procedure to history.

        Args:
            item: The completed procedure info.
        """
        self.beginResetModel()
        self._items.append(item)
        self._apply_filter()
        self.endResetModel()
        logger.debug(f"Added to recent: {item.name} ({item.status.value})")

    def set_status_filter(self, statuses: set[RecentStatus]) -> None:
        """Set which statuses to show.

        Args:
            statuses: Set of statuses to include.
        """
        self.beginResetModel()
        self._status_filter = statuses
        self._apply_filter()
        self.endResetModel()

    def _apply_filter(self) -> None:
        """Apply status filter to items."""
        self._filtered_items = [
            item for item in self._items if item.status in self._status_filter
        ]

    def get_item(self, row: int) -> RecentItem | None:
        """Get the item at a specific row.

        Args:
            row: Row index.

        Returns:
            The item or None if out of bounds.
        """
        if 0 <= row < len(self._filtered_items):
            return self._filtered_items[row]
        return None

    def clear(self) -> None:
        """Clear all history."""
        self.beginResetModel()
        self._items.clear()
        self._filtered_items.clear()
        self.endResetModel()

    # === QAbstractTableModel implementation ===

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        """Get number of rows."""
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._filtered_items)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: ARG002
        """Get number of columns."""
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """Get data for index."""
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if row >= len(self._filtered_items):
            return None

        item = self._filtered_items[row]

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:  # Plan name
                return item.name
            elif col == 1:  # Parameters
                return self._format_kwargs(item.kwargs)
            elif col == 2:  # Status
                return item.status.value.capitalize()
            elif col == 3:  # Duration
                return self._format_duration(item.duration_seconds)
            elif col == 4:  # Completed
                return item.completed_at.strftime("%H:%M:%S")
        elif role == Qt.ItemDataRole.ToolTipRole:
            if col == 1:  # Full parameters
                return json.dumps(item.kwargs, indent=2, default=str)
            elif col == 2 and item.error:  # Error message
                return item.error
            elif col == 4:  # Full timestamp
                return item.completed_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        elif role == Qt.ItemDataRole.ForegroundRole:
            if item.status == RecentStatus.FAILED:
                from PySide6.QtGui import QColor

                return QColor(Qt.GlobalColor.red)
            elif item.status == RecentStatus.CANCELLED:
                from PySide6.QtGui import QColor

                return QColor(Qt.GlobalColor.darkYellow)
        elif role == Qt.ItemDataRole.UserRole:
            return item

        return None

    def _format_kwargs(self, kwargs: dict[str, Any]) -> str:
        """Format kwargs for display."""
        if not kwargs:
            return ""
        parts = []
        for key, value in list(kwargs.items())[:3]:
            if isinstance(value, (list, tuple)):
                value_str = f"[{len(value)} items]"
            elif isinstance(value, str) and len(value) > 20:
                value_str = value[:17] + "..."
            else:
                value_str = str(value)
            parts.append(f"{key}={value_str}")
        result = ", ".join(parts)
        if len(kwargs) > 3:
            result += f", +{len(kwargs) - 3} more"
        return result

    def _format_duration(self, seconds: float) -> str:
        """Format duration for display.

        Args:
            seconds: Duration in seconds.

        Returns:
            Formatted string like "1m 23s" or "45.2s".
        """
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        if minutes < 60:
            return f"{minutes}m {secs}s"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m"

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        """Get header data."""
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """Get item flags."""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class RecentTableView(QTableView):
    """Table view for recent history with filtering and context menu.

    Signals:
        retry_requested(RecentItem): Emitted when user wants to retry an item.
        duplicate_requested(RecentItem): Emitted when user wants to re-queue.
    """

    retry_requested = Signal(object)  # RecentItem
    duplicate_requested = Signal(object)  # RecentItem

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the view.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._setup_view()

    def _setup_view(self) -> None:
        """Configure the view settings."""
        # Selection
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # Appearance
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)

        # Context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def setModel(self, model: RecentModel) -> None:
        """Set the model and configure columns.

        Args:
            model: The recent model.
        """
        super().setModel(model)

        # Configure column widths
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Plan
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Params
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Status
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Duration
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Completed

    def _show_context_menu(self, pos: QPoint) -> None:
        """Show context menu at position."""
        index = self.indexAt(pos)
        if not index.isValid():
            return

        item = index.data(Qt.ItemDataRole.UserRole)
        if item is None:
            return

        menu = QMenu(self)

        # Retry is only shown for failed items
        if item.status == RecentStatus.FAILED:
            retry_action = menu.addAction("Retry")
            retry_action.triggered.connect(lambda: self.retry_requested.emit(item))
            menu.addSeparator()

        duplicate_action = menu.addAction("Add to Queue")
        duplicate_action.triggered.connect(lambda: self.duplicate_requested.emit(item))

        menu.exec(self.viewport().mapToGlobal(pos))
