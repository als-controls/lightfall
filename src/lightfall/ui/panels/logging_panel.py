"""Logging panel for viewing application logs.

Provides a real-time log viewer with level filtering capabilities
and clickable code locations for opening files in the configured editor.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from loguru import logger
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QCursor, QFont
from PySide6.QtWidgets import (
    QHeaderView,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)

from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.utils.editor_launcher import CodeEditor, get_editor_from_string, open_in_editor
from lightfall.utils.module_resolver import resolve_module_path


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

# Selectable log levels, in display order
LEVELS = ["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]

# Default minimum level shown when the panel starts
DEFAULT_LEVEL = "DEBUG"

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

# Link color for Location column
LINK_COLOR = QColor(30, 100, 200)  # Blue link color
LINK_COLOR_DARK = QColor(100, 150, 255)  # Blue link color for dark themes


class LocationDelegate(QStyledItemDelegate):
    """Custom delegate for the Location column to style it as a clickable link."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

    def paint(
        self,
        painter: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """Paint the cell with link styling."""
        # Get the text
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text is None:
            super().paint(painter, option, index)
            return

        # Setup painter
        painter.save()

        # Draw selection background if selected
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            painter.setPen(option.palette.highlightedText().color())
        else:
            # Use link color for unselected items
            painter.setPen(LINK_COLOR)

        # Set underlined font
        font = QFont(option.font)
        font.setUnderline(True)
        painter.setFont(font)

        # Draw text
        text_rect = option.rect.adjusted(4, 0, -4, 0)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

        painter.restore()


class LogTableModel(QAbstractTableModel):
    """Table model for log records with level filtering."""

    COLUMNS = ["Time", "Level", "Location", "Message"]
    LOCATION_COLUMN = 2  # Index of the Location column
    MAX_RECORDS = 10000  # Limit memory usage

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_records: deque[LogRecord] = deque(maxlen=self.MAX_RECORDS)
        self._filtered_records: list[LogRecord] = []
        self._min_level: int = LEVEL_NUMBERS["DEBUG"]

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._filtered_records)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
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
            # Location column link color is handled by LocationDelegate
        elif role == Qt.ItemDataRole.ToolTipRole:
            if col == 2:  # Location tooltip
                return "Double-click to open in editor, right-click for options"
            elif col == 3:  # Full message in tooltip
                return record.message
        elif role == Qt.ItemDataRole.UserRole:
            # Return the full LogRecord for custom processing
            return record

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

    def get_record(self, row: int) -> LogRecord | None:
        """Get the log record at the given row.

        Args:
            row: Row index in the filtered records.

        Returns:
            LogRecord or None if row is out of bounds.
        """
        if 0 <= row < len(self._filtered_records):
            return self._filtered_records[row]
        return None


class LoggingPanel(BasePanel):
    """Panel for viewing application logs.

    Displays log messages in real-time with level filtering.
    Uses loguru's sink mechanism to capture log records.
    Double-click on Location column to open the file in your configured editor.

    Example:
        >>> panel = LoggingPanel()
        >>> # Panel automatically captures logs
        >>> logger.info("This will appear in the panel")
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.logging",
        name="Logging",
        description="View application logs with level filtering",
        icon="scroll",
        category="System",
        singleton=True,
        closable=True,
        keywords=["logging", "logs", "debug", "trace", "errors", "console"],
        # Docking preferences - bottom sidebar (auto-hide icons on bottom edge)
        default_area="bottom",
        sidebar_group="top",
        auto_hide=True,
        sidebar_order=2,
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
        self._current_level = DEFAULT_LEVEL
        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Set up the panel UI."""
        # Title bar: log-level filter menu. Checkable actions in an
        # exclusive group indicate the current level.
        level_menu = QMenu()
        self._level_group = QActionGroup(level_menu)
        self._level_group.setExclusive(True)
        self._level_actions: dict[str, QAction] = {}
        for level in LEVELS:
            act = QAction(level, level_menu)
            act.setCheckable(True)
            act.setChecked(level == self._current_level)
            act.triggered.connect(lambda _checked=False, level=level: self._on_level_changed(level))
            self._level_group.addAction(act)
            level_menu.addAction(act)
            self._level_actions[level] = act
        self.add_title_bar_button("mdi6.filter", "Log level", menu=level_menu)

        # Title bar: auto-scroll toggle.
        self._auto_scroll_action = self.add_title_bar_button(
            "mdi6.auto-download",
            "Auto-scroll",
            self._on_auto_scroll_toggled,
            checkable=True,
            checked=self._auto_scroll,
        )

        # Title bar: clear button.
        self.add_title_bar_button("mdi6.trash-can", "Clear", self._on_clear)

        # Table view
        self._model = LogTableModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        # Set custom delegate for Location column to show as clickable link
        self._location_delegate = LocationDelegate(self._table)
        self._table.setItemDelegateForColumn(LogTableModel.LOCATION_COLUMN, self._location_delegate)

        # Enable mouse tracking to show pointer cursor over Location column
        self._table.setMouseTracking(True)
        self._table.viewport().installEventFilter(self)

        # Connect double-click to open in editor
        self._table.doubleClicked.connect(self._on_double_click)

        # Enable context menu
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)

        # Column sizing
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Time
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Level
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Location
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)  # Message

        # Layout
        self._layout.addWidget(self._table)

        # Connect signal for thread-safe record addition
        self._record_received.connect(self._add_record)

        # Install log handler
        self._install_handler()

    def eventFilter(self, obj: Any, event: Any) -> bool:
        """Handle mouse events to change cursor over Location column."""
        from PySide6.QtCore import QEvent

        if obj == self._table.viewport() and event.type() == QEvent.Type.MouseMove:
            pos = event.position().toPoint()
            index = self._table.indexAt(pos)
            if index.isValid() and index.column() == LogTableModel.LOCATION_COLUMN:
                self._table.viewport().setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            else:
                self._table.viewport().setCursor(QCursor(Qt.CursorShape.ArrowCursor))

        return super().eventFilter(obj, event)

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
        self._current_level = level
        # Keep the menu checkmark in sync (e.g. for programmatic changes).
        action = self._level_actions.get(level)
        if action is not None and not action.isChecked():
            action.setChecked(True)
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

    @Slot(QModelIndex)
    def _on_double_click(self, index: QModelIndex) -> None:
        """Handle double-click on table cells.

        Opens the file in the configured editor when Location column is double-clicked.
        """
        if index.column() != LogTableModel.LOCATION_COLUMN:
            return

        record = self._model.get_record(index.row())
        if record is None:
            return

        self._open_in_editor(record)

    def _on_context_menu(self, pos: Any) -> None:
        """Show context menu with editor options."""
        index = self._table.indexAt(pos)
        if not index.isValid():
            return

        # Only show context menu for Location column
        if index.column() != LogTableModel.LOCATION_COLUMN:
            return

        record = self._model.get_record(index.row())
        if record is None:
            return

        menu = QMenu(self._table)

        # Add "Open in VSCode" action
        vscode_action = QAction("Open in VSCode", menu)
        vscode_action.triggered.connect(lambda: self._open_in_editor(record, CodeEditor.VSCODE))
        menu.addAction(vscode_action)

        # Add "Open in PyCharm" action
        pycharm_action = QAction("Open in PyCharm", menu)
        pycharm_action.triggered.connect(lambda: self._open_in_editor(record, CodeEditor.PYCHARM))
        menu.addAction(pycharm_action)

        menu.addSeparator()

        # Add "Copy Location" action
        copy_action = QAction("Copy Location", menu)
        copy_action.triggered.connect(lambda: self._copy_location(record))
        menu.addAction(copy_action)

        # Show menu at cursor position
        menu.exec_(self._table.viewport().mapToGlobal(pos))

    def _open_in_editor(self, record: LogRecord, editor: CodeEditor | None = None) -> None:
        """Open the source file at the log record's line in the editor.

        Args:
            record: The log record containing module, function, and line info.
            editor: The editor to use, or None to use the configured default.
        """
        # Resolve module to file path
        file_path = resolve_module_path(record.module)
        if file_path is None:
            logger.warning("Could not resolve module path for: {}", record.module)
            from lightfall.ui.toast import ToastManager

            ToastManager.get_instance().warning(
                "Cannot open location",
                f"Could not resolve module: {record.module}",
            )
            return

        # Get editor preference if not specified
        if editor is None:
            prefs = PreferencesManager.get_instance()
            editor_str = prefs.get("code_editor", CodeEditor.VSCODE.value)
            editor = get_editor_from_string(editor_str)
            if editor is None:
                editor = CodeEditor.VSCODE

        # Open in editor
        success = open_in_editor(file_path, record.line, editor)
        if success:
            logger.debug("Opened {}:{} in {}", file_path, record.line, editor.value)
        else:
            from lightfall.ui.toast import ToastManager

            ToastManager.get_instance().error(
                "Failed to open editor",
                f"Could not open {editor.value}",
            )

    def _copy_location(self, record: LogRecord) -> None:
        """Copy the location string to the clipboard.

        Args:
            record: The log record.
        """
        from PySide6.QtWidgets import QApplication

        location = f"{record.module}:{record.function}:{record.line}"
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(location)
            logger.debug("Copied location to clipboard: {}", location)

    def _on_closing(self) -> None:
        """Clean up when panel closes."""
        if self._handler_id is not None:
            logger.remove(self._handler_id)
            self._handler_id = None

    def restore_state(self, state: dict[str, Any]) -> None:
        """Restore panel state."""
        super().restore_state(state)

        # Restore level filter
        level = state.get("level_filter", DEFAULT_LEVEL)
        if level in self._level_actions:
            self._level_actions[level].setChecked(True)
            self._on_level_changed(level)

        # Restore auto-scroll
        auto_scroll = state.get("auto_scroll", True)
        self._auto_scroll_action.setChecked(auto_scroll)
        self._on_auto_scroll_toggled(auto_scroll)

    # === Introspection API for MCP tools ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get panel-specific introspection data."""
        return {
            "total_records": self._model.record_count(),
            "filtered_records": self._model.filtered_count(),
            "level_filter": self._current_level,
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

    def action_set_level(self, level: str = DEFAULT_LEVEL) -> bool:
        """Set level action handler for MCP tools."""
        if level in self._level_actions:
            self._level_actions[level].setChecked(True)
            self._on_level_changed(level)
            return True
        return False

    def action_toggle_auto_scroll(self) -> bool:
        """Toggle auto-scroll action handler for MCP tools."""
        self._auto_scroll_action.toggle()
        self._on_auto_scroll_toggled(self._auto_scroll_action.isChecked())
        return True
