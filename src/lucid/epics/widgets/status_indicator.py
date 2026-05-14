"""StatusIndicator -- small circular status dot for connection/alarm state."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QWidget

# Fallback colors used when ThemeManager isn't reachable (early init or
# headless contexts). These match LIGHT_COLORS from lucid.ui.theme.
_FALLBACK = {
    "off": "#6b7280",
    "on": "#16a34a",
    "warning": "#d97706",
    "error": "#dc2626",
    "disconnected": "#dc2626",
}


def _state_colors() -> dict[str, str]:
    """Resolve state -> hex from the active ThemeManager.

    Pulls the *vivid* status colors (success/warning/error) rather than the
    background-tint variants in ``lucid.epics.widgets.style`` so the dot
    reads clearly on both light and dark themes.
    """
    try:
        from lucid.ui.theme import ThemeManager

        c = ThemeManager.get_instance().colors
        return {
            "off": c.text_secondary,
            "on": c.success,
            "warning": c.warning,
            "error": c.error,
            "disconnected": c.disconnected or c.error,
        }
    except Exception:
        return dict(_FALLBACK)


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
        self._connect_theme_signal()

    def set_state(self, state: str) -> None:
        """Set indicator state: 'off', 'on', 'warning', 'error', 'disconnected'."""
        self._state = state
        self._update_style()

    def set_connected(self, connected: bool) -> None:
        """Set connected/disconnected state."""
        self.set_state("on" if connected else "error")

    def set_color(self, color: str) -> None:
        """Set a custom color directly (bypasses theme resolution)."""
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: {self._radius}px;
                border: 1px solid #333;
            }}
        """)

    def _update_style(self) -> None:
        colors = _state_colors()
        color = colors.get(self._state, colors["off"])
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: {self._radius}px;
                border: 1px solid #333;
            }}
        """)

    def _connect_theme_signal(self) -> None:
        try:
            from lucid.ui.theme import ThemeManager

            ThemeManager.get_instance().colors_changed.connect(self._update_style)
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            from lucid.ui.theme import ThemeManager

            ThemeManager.get_instance().colors_changed.disconnect(self._update_style)
        except Exception:
            pass
        super().closeEvent(event)
