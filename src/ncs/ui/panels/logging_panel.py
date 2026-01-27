"""Logging panel for viewing application logs.

Provides a real-time log viewer with level filtering capabilities.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from loguru import logger
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ncs.ui.panels.base import BasePanel, PanelMetadata


@dataclass
class LogRecord:
    """Represents a single log entry."""

    timestamp: datetime
    level: str
    level_no: int
    module: str
    function: str
    line: int
    message: str


# Log level numbers for filtering (matching loguru's level numbers)
LEVEL_NUMBERS = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 25,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

# Level colors for display
LEVEL_COLORS = {
    "TRACE": QColor(128, 128, 128),  # Gray
    "DEBUG": QColor(0, 180, 180),  # Cyan
    "INFO": QColor(0, 180, 0),  # Green
    "SUCCESS": QColor(0, 220, 0),  # Bright green
    "WARNING": QColor(220, 180, 0),  # Yellow
    "ERROR": QColor(220, 0, 0),  # Red
    "CRITICAL": QColor(180, 0, 0),  # Dark red
}


class LogTableModel(QAbstractTableModel):
    """Table model for log records with level filtering."""

    COLUMNS = ["Time", "Level", "Location", "Message"]
    MAX_RECORDS = 10000  # Limit memory usage

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_records: deque[LogRecord] = deque(maxlen=self.MAX_RECORDS)
        self._filtered_records: list[LogRecord] = []
        self._min_level: int = LEVEL_NUMBERS["DEBUG"]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._filtered_records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        record = self._filtered_records[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:  # Time
                return record.timestamp.strftime("%H:%M:%S.%f")[:-3]
            elif col == 1:  # Level
                return record.level
            elif col == 2:  # Location
                return f"{record.module}:{record.function}:{record.line}"
            elif col == 3:  # Message
                return record.message
        elif role == Qt.ItemDataRole.ForegroundRole:
            if col == 1:  # Color the level column
                return QBrush(LEVEL_COLORS.get(record.level, QColor(200, 200, 200)))
        elif role == Qt.ItemDataRole.ToolTipRole:
            if col == 3:  # Full message in tooltip
                return record.message

        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.COLUMNS[section]
        return None

    def add_record(self, record: LogRecord) -> None:
        """Add a new log record."""
        self._all_records.append(record)

        # Check if it passes the filter
        if record.level_no >= self._min_level:
            self.beginInsertRows(QModelIndex(), len(self._filtered_records), len(self._filtered_records))
            self._filtered_records.append(record)
            self.endInsertRows()

    def set_min_level(self, level: str) -> None:
        """Set the minimum log level to display."""
        self._min_level = LEVEL_NUMBERS.get(level, LEVEL_NUMBERS["DEBUG"])
        self._refilter()

    def _refilter(self) -> None:
        """Refilter all records based on current level setting."""
        self.beginResetModel()
        self._filtered_records = [r for r in self._all_records if r.level_no >= self._min_level]
        self.endResetModel()

    def clear(self) -> None:
        """Clear all log records."""
        self.beginResetModel()
        self._all_records.clear()
        self._filtered_records.clear()
        self.endResetModel()

    def record_count(self) -> int:
        """Return total record count (before filtering)."""
        return len(self._all_records)

    def filtered_count(self) -> int:
        """Return filtered record count."""
        return len(self._filtered_records)


class LoggingPanel(BasePanel):
    """Panel for viewing application logs.

    Displays log messages in real-time with level filtering.
    Uses loguru's sink mechanism to capture log records.

    Example:
        >>> panel = LoggingPanel()
        >>> # Panel automatically captures logs
        >>> logger.info("This will appear in the panel")
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="ncs.panels.logging",
        name="Logging",
        description="View application logs with level filtering",
        icon="file-text",
        category="System",
        singleton=True,
        closable=True,
        keywords=["logging", "logs", "debug", "trace", "errors", "console"],
    )

    # Signal to safely add records from any thread
    _record_received = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the Logging panel.

        Args:
            parent: Parent widget.
        """
        self._handler_id: int | None = None
        self._auto_scroll = True
        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Set up the panel UI."""
        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 4, 4, 4)
        toolbar.setSpacing(8)

        # Level filter combo
        self._level_combo = QComboBox()
        self._level_combo.addItems(["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"])
        self._level_combo.setCurrentText("DEBUG")
        self._level_combo.currentTextChanged.connect(self._on_level_changed)
        toolbar.addWidget(self._level_combo)

        # Auto-scroll toggle
        self._auto_scroll_btn = QPushButton("Auto-scroll")
        self._auto_scroll_btn.setCheckable(True)
        self._auto_scroll_btn.setChecked(True)
        self._auto_scroll_btn.toggled.connect(self._on_auto_scroll_toggled)
        toolbar.addWidget(self._auto_scroll_btn)

        # Clear button
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._on_clear)
        toolbar.addWidget(self._clear_btn)

        toolbar.addStretch()

        # Table view
        self._model = LogTableModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        # Column sizing
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Time
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Level
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Location
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)  # Message

        # Layout
        self._layout.addLayout(toolbar)
        self._layout.addWidget(self._table)

        # Connect signal for thread-safe record addition
        self._record_received.connect(self._add_record)

        # Install log handler
        self._install_handler()

    def _install_handler(self) -> None:
        """Install loguru sink to capture log records."""
        self._handler_id = logger.add(
            self._log_sink,
            level="TRACE",  # Capture all levels, filter in UI
            format="{message}",  # We handle formatting ourselves
        )
        logger.debug("LoggingPanel handler installed")

    def _log_sink(self, message) -> None:
        """Loguru sink that emits records to the UI.

        This runs in the logging thread, so we emit a signal
        to safely update the UI in the main thread.
        """
        record = message.record
        log_record = LogRecord(
            timestamp=record["time"].replace(tzinfo=None),
            level=record["level"].name,
            level_no=record["level"].no,
            module=record["name"],
            function=record["function"],
            line=record["line"],
            message=record["message"],
        )
        self._record_received.emit(log_record)

    @Slot(object)
    def _add_record(self, record: LogRecord) -> None:
        """Add a log record to the model (main thread)."""
        self._model.add_record(record)

        # Auto-scroll to bottom if enabled
        if self._auto_scroll:
            self._table.scrollToBottom()

    @Slot(str)
    def _on_level_changed(self, level: str) -> None:
        """Handle level filter change."""
        self._model.set_min_level(level)
        self.set_state("level_filter", level)

    @Slot(bool)
    def _on_auto_scroll_toggled(self, checked: bool) -> None:
        """Handle auto-scroll toggle."""
        self._auto_scroll = checked
        self.set_state("auto_scroll", checked)
        if checked:
            self._table.scrollToBottom()

    @Slot()
    def _on_clear(self) -> None:
        """Clear all log records."""
        self._model.clear()
        logger.debug("Log panel cleared")

    def _on_closing(self) -> None:
        """Clean up when panel closes."""
        if self._handler_id is not None:
            logger.remove(self._handler_id)
            self._handler_id = None

    def restore_state(self, state: dict[str, Any]) -> None:
        """Restore panel state."""
        super().restore_state(state)

        # Restore level filter
        level = state.get("level_filter", "DEBUG")
        self._level_combo.setCurrentText(level)

        # Restore auto-scroll
        auto_scroll = state.get("auto_scroll", True)
        self._auto_scroll_btn.setChecked(auto_scroll)

    # === Introspection API for MCP tools ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get panel-specific introspection data."""
        return {
            "total_records": self._model.record_count(),
            "filtered_records": self._model.filtered_count(),
            "level_filter": self._level_combo.currentText(),
            "auto_scroll": self._auto_scroll,
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for this panel."""
        base_actions = super()._get_available_actions()
        return base_actions + [
            {
                "name": "clear",
                "description": "Clear all log messages",
                "method": "action_clear",
            },
            {
                "name": "set_level",
                "description": "Set minimum log level to display",
                "method": "action_set_level",
                "params": {"level": "TRACE|DEBUG|INFO|SUCCESS|WARNING|ERROR|CRITICAL"},
            },
            {
                "name": "toggle_auto_scroll",
                "description": "Toggle auto-scroll behavior",
                "method": "action_toggle_auto_scroll",
            },
        ]

    def action_clear(self) -> bool:
        """Clear action handler for MCP tools."""
        self._on_clear()
        return True

    def action_set_level(self, level: str = "DEBUG") -> bool:
        """Set level action handler for MCP tools."""
        if level in LEVEL_NUMBERS:
            self._level_combo.setCurrentText(level)
            return True
        return False

    def action_toggle_auto_scroll(self) -> bool:
        """Toggle auto-scroll action handler for MCP tools."""
        self._auto_scroll_btn.toggle()
        return True
