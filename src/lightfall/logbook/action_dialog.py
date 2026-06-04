"""
Dialog for displaying expanded action group details.

Shows the full list of device actions in an action group when the user
clicks on a collapsed action group summary in the logbook.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lightfall.logbook.style import (
    get_action_group_background_color,
    get_action_group_border_color,
)

if TYPE_CHECKING:
    from lightfall.logbook.action_logger import DeviceAction


class ActionGroupDialog(QDialog):
    """
    Dialog showing expanded action group details.

    Displays a table of all device actions in an action group,
    including timestamps, device names, action types, and value changes.

    Example:
        >>> actions = [DeviceAction("motor1", "set", 0.0, 10.0), ...]
        >>> dialog = ActionGroupDialog("group-123", actions, parent)
        >>> dialog.exec()
    """

    def __init__(
        self,
        group_id: str,
        actions: list[DeviceAction],
        parent: QWidget | None = None,
    ) -> None:
        """
        Initialize the action group dialog.

        Args:
            group_id: The action group ID.
            actions: List of DeviceAction objects to display.
            parent: Parent widget.
        """
        super().__init__(parent)
        self._group_id = group_id
        self._actions = actions

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the dialog UI."""
        self.setWindowTitle("Device Actions")
        self.setMinimumSize(500, 300)
        self.resize(600, 400)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header
        if self._actions:
            start_time = self._actions[0].timestamp.strftime("%H:%M:%S")
            end_time = self._actions[-1].timestamp.strftime("%H:%M:%S")
            time_range = f"{start_time} - {end_time}" if start_time != end_time else start_time
            header_text = f"<b>{len(self._actions)} Device Actions</b> ({time_range})"
        else:
            header_text = "<b>No Actions</b>"

        header = QLabel(header_text)
        header.setStyleSheet(self._get_header_style())
        layout.addWidget(header)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Time", "Device", "Action", "Change"])
        self._table.setRowCount(len(self._actions))
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Populate table
        for row, action in enumerate(self._actions):
            time_item = QTableWidgetItem(action.timestamp.strftime("%H:%M:%S"))
            device_item = QTableWidgetItem(action.device_name)
            action_item = QTableWidgetItem(action.action_type)
            change_item = QTableWidgetItem(action.format_change() or "—")

            self._table.setItem(row, 0, time_item)
            self._table.setItem(row, 1, device_item)
            self._table.setItem(row, 2, action_item)
            self._table.setItem(row, 3, change_item)

        # Resize columns to content
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self._table)

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _get_header_style(self) -> str:
        """Get stylesheet for the header label."""
        bg = get_action_group_background_color()
        border = get_action_group_border_color()
        return f"""
            QLabel {{
                background-color: {bg};
                border-left: 3px solid {border};
                padding: 8px 12px;
                font-size: 11pt;
            }}
        """


class ActionGroupSummaryWidget(QWidget):
    """
    Widget displaying a clickable action group summary.

    This widget can be used as an alternative to the HTML-based rendering
    for better click handling in the logbook.
    """

    def __init__(
        self,
        group_id: str,
        count: int,
        time_range: str,
        parent: QWidget | None = None,
    ) -> None:
        """
        Initialize the summary widget.

        Args:
            group_id: The action group ID.
            count: Number of actions in the group.
            time_range: Formatted time range string.
            parent: Parent widget.
        """
        super().__init__(parent)
        self._group_id = group_id
        self._count = count
        self._time_range = time_range
        self._expanded = False

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        bg = get_action_group_background_color()
        border = get_action_group_border_color()

        self._label = QLabel()
        self._update_label_text()
        self._label.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                border-left: 3px solid {border};
                padding: 8px 12px;
            }}
        """)
        self._label.setCursor(Qt.CursorShape.PointingHandCursor)

        layout.addWidget(self._label)

    def _update_label_text(self) -> None:
        """Update the label text based on expanded state."""
        icon = "[-]" if self._expanded else "[+]"
        self._label.setText(
            f'<span style="font-family: monospace; font-weight: bold;">{icon}</span> '
            f"<b>Device Actions</b> ({self._count} actions, {self._time_range})"
        )

    @property
    def group_id(self) -> str:
        """Get the action group ID."""
        return self._group_id

    def mousePressEvent(self, event) -> None:
        """Handle mouse press to toggle expanded state."""
        self._expanded = not self._expanded
        self._update_label_text()
        super().mousePressEvent(event)
