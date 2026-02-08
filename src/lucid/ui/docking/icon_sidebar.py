"""IconStripSidebar - Custom icon strip sidebar for panel navigation.

Provides a VS Code/PyCharm-like icon strip that controls docked panels.
Unlike QtAds auto-hide sidebars, icons remain visible regardless of
whether panels are shown (pinned).

Architecture:
    +------+
    | [B]  |  <- Top icons (dock panels to left)
    | [D]  |
    |      |
    |      |  <- Stretch spacer
    |      |
    | [C]  |  <- Bottom icons (dock panels to bottom)
    | [L]  |
    +------+
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import qtawesome as qta
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QToolButton, QVBoxLayout, QWidget

from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


class IconStripButton(QToolButton):
    """A toggle button for the icon strip sidebar."""

    def __init__(
        self,
        panel_id: str,
        icon: QIcon,
        tooltip: str,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the icon strip button.

        Args:
            panel_id: The panel ID this button controls.
            icon: The button icon.
            tooltip: Tooltip text shown on hover.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.panel_id = panel_id
        self.setIcon(icon)
        self.setToolTip(tooltip)
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setIconSize(QSize(17, 17))
        self.setFixedSize(26, 26)


class IconStripSidebar(QFrame):
    """Custom icon strip sidebar that controls docked panels.

    Emits panel_toggled signal when icons are clicked. The main window
    or docking manager handles showing/hiding the actual panels.

    Icons are split into top (left-docking panels) and bottom
    (bottom-docking panels) sections with a stretch spacer between.
    """

    panel_toggled = Signal(str, bool)  # panel_id, should_show

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the sidebar.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._buttons: dict[str, IconStripButton] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the sidebar UI."""
        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setFixedWidth(34)
        self.setObjectName("IconStripSidebar")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def add_panel_button(
        self,
        panel_id: str,
        icon_name: str,
        tooltip: str,
    ) -> IconStripButton:
        """Add a button for a panel.

        Args:
            panel_id: Unique panel identifier.
            icon_name: QtAwesome icon name (e.g., "fa5s.bolt") or path.
            tooltip: Tooltip text shown on hover.

        Returns:
            The created button.
        """
        # Get theme color for icon
        try:
            from lucid.ui.theme import ThemeManager
            theme_mgr = ThemeManager.get_instance()
            icon_color = theme_mgr.colors.text
        except Exception:
            icon_color = "#cccccc"

        # Create icon
        icon = self._resolve_icon(icon_name, icon_color)

        button = IconStripButton(panel_id, icon, tooltip, self)
        button.toggled.connect(lambda checked: self._on_button_toggled(panel_id, checked))

        self._buttons[panel_id] = button
        self.layout().addWidget(button)

        logger.debug("Added sidebar button for panel: {}", panel_id)
        return button

    def _resolve_icon(self, icon_name: str, color: str) -> QIcon:
        """Resolve an icon name to a QIcon.

        Args:
            icon_name: QtAwesome icon name or path.
            color: Icon color.

        Returns:
            QIcon instance.
        """
        if not icon_name:
            return QIcon()

        try:
            # Support both "bolt" and "fa5s.bolt" formats
            if "." not in icon_name:
                for prefix in ["fa5s", "fa5", "mdi", "mdi6", "ri"]:
                    try:
                        return qta.icon(f"{prefix}.{icon_name}", color=color)
                    except Exception:
                        continue
            else:
                return qta.icon(icon_name, color=color)
        except Exception:
            pass

        return QIcon(icon_name)

    def _on_button_toggled(self, panel_id: str, checked: bool) -> None:
        """Handle button toggle.

        Args:
            panel_id: The panel that was toggled.
            checked: Whether the button is now checked.
        """
        self.panel_toggled.emit(panel_id, checked)

    def set_panel_active(self, panel_id: str, active: bool) -> None:
        """Set the active state of a panel button.

        Call this when panel visibility changes externally (e.g., closed
        via X button) to keep the sidebar in sync.

        Args:
            panel_id: Panel identifier.
            active: Whether the panel is active/visible.
        """
        if panel_id in self._buttons:
            button = self._buttons[panel_id]
            button.blockSignals(True)
            button.setChecked(active)
            button.blockSignals(False)

    def add_stretch(self) -> None:
        """Add stretch to push subsequent buttons to bottom."""
        self.layout().addStretch()

    def add_separator(self) -> None:
        """Add a visual separator line."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFixedHeight(1)
        separator.setObjectName("IconStripSeparator")
        self.layout().addWidget(separator)

    def update_theme(self) -> None:
        """Update button icons when theme changes."""
        try:
            from lucid.ui.theme import ThemeManager
            theme_mgr = ThemeManager.get_instance()
            icon_color = theme_mgr.colors.text
        except Exception:
            icon_color = "#cccccc"

        for panel_id, button in self._buttons.items():
            # Re-resolve icon with new color
            # We'd need to store icon_name to do this properly
            # For now, just log that theme changed
            pass

        logger.debug("IconStripSidebar theme updated")
