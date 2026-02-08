"""PanelDockWidget - CDockWidget specialized for NCS panels.

Wraps panels in QtAds dock widgets with proper icon, title, and feature
configuration based on panel metadata.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import qtawesome as qta
from PySide6.QtGui import QIcon
from PySide6QtAds import CDockWidget

from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from lucid.ui.panels.base import BasePanel


def resolve_panel_icon(icon_name: str, size: int = 19) -> QIcon:
    """Resolve a panel icon name to a QIcon.

    Args:
        icon_name: Icon name (QtAwesome name like "fa5s.bolt" or path).
        size: Icon size in pixels for crisp rendering.

    Returns:
        QIcon instance.
    """
    if not icon_name:
        return QIcon()

    # Get theme color for icon
    try:
        from lucid.ui.theme import ThemeManager
        theme_mgr = ThemeManager.get_instance()
        icon_color = theme_mgr.colors.text
    except Exception:
        icon_color = "#d4d4d4"  # Default to light gray

    # Try FontAwesome icon first
    try:
        # Support both "bolt" and "fa5s.bolt" formats
        if "." not in icon_name:
            # Try common prefixes
            for prefix in ["fa5s", "fa5", "mdi", "mdi6", "ri"]:
                try:
                    return qta.icon(f"{prefix}.{icon_name}", color=icon_color)
                except Exception:
                    continue
        else:
            return qta.icon(icon_name, color=icon_color)
    except Exception:
        pass

    # Fall back to file path
    return QIcon(icon_name)


class PanelDockWidget(CDockWidget):
    """CDockWidget specialized for NCS panels.

    Wraps a BasePanel in a QtAds dock widget with:
    - Icon from panel metadata
    - Title from panel metadata
    - Feature flags based on closable setting
    - Lifecycle signal forwarding
    """

    def __init__(self, panel: BasePanel, parent: QWidget | None = None) -> None:
        """Initialize the panel dock widget.

        Args:
            panel: The BasePanel instance to wrap.
            parent: Optional parent widget.
        """
        super().__init__(panel.panel_metadata.name, parent)
        self._panel = panel

        # Set object name for state persistence
        self.setObjectName(f"dock_{panel.panel_metadata.id}")

        # Set content widget
        self.setWidget(panel)

        # Set icon from panel metadata
        if panel.panel_metadata.icon:
            icon = resolve_panel_icon(panel.panel_metadata.icon)
            if not icon.isNull():
                self.setIcon(icon)

        # Set tooltip to panel name (shown on hover in icon-only mode)
        self.setToolTip(panel.panel_metadata.name)

        # Configure features based on metadata
        features = CDockWidget.DefaultDockWidgetFeatures
        if not panel.panel_metadata.closable:
            features = features & ~CDockWidget.DockWidgetClosable

        self.setFeature(CDockWidget.DockWidgetDeleteOnClose, False)
        self.setFeatures(features)

        # Connect visibility to panel lifecycle
        self.visibilityChanged.connect(self._on_visibility_changed)

        logger.debug("Created PanelDockWidget for {}", panel.panel_metadata.id)

    @property
    def panel(self) -> BasePanel:
        """Get the wrapped panel."""
        return self._panel

    @property
    def panel_id(self) -> str:
        """Get the panel ID."""
        return self._panel.panel_metadata.id

    def _on_visibility_changed(self, visible: bool) -> None:
        """Handle visibility changes."""
        if visible:
            self._panel.activate()
        else:
            self._panel.deactivate()
