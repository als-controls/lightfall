"""StatusIndicator -- small circular status dot for connection/alarm state."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QWidget

from lucid.epics.widgets.style import (
    get_disconnected_color,
    get_error_color,
    get_success_color,
    get_warning_color,
)


class StatusIndicator(QFrame):
    """A small circular status indicator.

    States: 'off', 'on', 'warning', 'error', 'disconnected'.
    """

    def __init__(self, size: int = 14, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._state = "off"
        self._radius = size // 2
        self._update_style()

    def set_state(self, state: str) -> None:
        """Set indicator state: 'off', 'on', 'warning', 'error', 'disconnected'."""
        self._state = state
        self._update_style()

    def set_connected(self, connected: bool) -> None:
        """Set connected/disconnected state."""
        self.set_state("on" if connected else "error")

    def set_color(self, color: str) -> None:
        """Set a custom color directly."""
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: {self._radius}px;
                border: 1px solid #333;
            }}
        """)

    def _update_style(self) -> None:
        colors = {
            "off": "#666666",
            "on": get_success_color(),
            "warning": get_warning_color(),
            "error": get_error_color(),
            "disconnected": get_disconnected_color(),
        }
        color = colors.get(self._state, colors["off"])
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: {self._radius}px;
                border: 1px solid #333;
            }}
        """)
