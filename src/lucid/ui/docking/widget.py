"""PanelDockWidget - QDockWidget specialized for LUCID panels.

Wraps panels in QDockWidget with proper icon, title, and feature
configuration based on panel metadata.

When use_custom_title_bar=True, a PanelTitleBar is set via
QDockWidget.setTitleBarWidget() — Qt then uses it as both the visual
header and the native drag handle for undocking. No custom mouse
event tracking needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from lucid.utils.logging import logger

if TYPE_CHECKING:
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
        icon_color = "#d4d4d4"

    try:
        if "." not in icon_name:
            for prefix in ["fa5s", "fa5", "mdi", "mdi6", "ri"]:
                try:
                    return qta.icon(f"{prefix}.{icon_name}", color=icon_color)
                except Exception:
                    continue
        else:
            return qta.icon(icon_name, color=icon_color)
    except Exception:
        pass

    return QIcon(icon_name)


class PanelTitleBar(QFrame):
    """Custom title bar for side panels.

    When set via QDockWidget.setTitleBarWidget(), Qt uses this as
    both the visual header and the drag handle for undocking.
    No custom mouse event handling needed — Qt does it all.

    Signals:
        close_requested: Emitted when the close button is clicked.
    """

    close_requested = Signal()

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        closable: bool = True,
    ) -> None:
        """Initialize the title bar.

        Args:
            title: The panel title to display.
            parent: Optional parent widget.
            closable: Whether to show the close button.
        """
        super().__init__(parent)
        self.setObjectName("PanelTitleBar")
        self.setFixedHeight(28)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(4)

        # Title label
        label = QLabel(title)
        label.setObjectName("PanelTitleLabel")
        layout.addWidget(label)
        layout.addStretch()

        # Close button
        if closable:
            self._close_btn = QToolButton()
            self._close_btn.setObjectName("PanelTitleCloseButton")
            self._close_btn.setFixedSize(20, 20)
            self._close_btn.setCursor(Qt.CursorShape.ArrowCursor)
            self._close_btn.clicked.connect(self.close_requested.emit)
            try:
                from lucid.ui.theme import ThemeManager
                theme_mgr = ThemeManager.get_instance()
                icon_color = theme_mgr.colors.text_secondary
            except Exception:
                icon_color = "#808080"
            try:
                self._close_btn.setIcon(qta.icon("mdi.close", color=icon_color))
            except Exception:
                self._close_btn.setText("x")
            layout.addWidget(self._close_btn)


class PanelDockWidget(QDockWidget):
    """QDockWidget specialized for LUCID panels.

    Wraps a BasePanel in a QDockWidget with:
    - Icon from panel metadata
    - Title from panel metadata
    - Feature flags based on closable setting
    - Optional custom title bar (set as Qt's title bar widget for
      native drag support)
    - Lifecycle signal forwarding

    Signals:
        dock_area_changed: Emitted when the widget moves to a different dock area.
    """

    dock_area_changed = Signal()

    def __init__(
        self,
        panel: BasePanel,
        parent: QWidget | None = None,
        *,
        use_custom_title_bar: bool = False,
    ) -> None:
        """Initialize the panel dock widget.

        Args:
            panel: The BasePanel instance to wrap.
            parent: Optional parent widget.
            use_custom_title_bar: If True, use a PanelTitleBar as Qt's
                title bar widget. Qt handles drag-to-undock natively.
        """
        super().__init__(panel.panel_metadata.name, parent)
        self._panel = panel
        self._title_bar: PanelTitleBar | None = None

        # Set object name for state persistence
        self.setObjectName(f"dock_{panel.panel_metadata.id}")

        # Set content widget
        self.setWidget(panel)

        # Configure features based on metadata
        features = (
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        if panel.panel_metadata.closable:
            features |= QDockWidget.DockWidgetFeature.DockWidgetClosable
        self.setFeatures(features)

        # Custom title bar — set as Qt's title bar widget so it
        # gets native drag-to-undock behavior for free
        if use_custom_title_bar:
            self._title_bar = PanelTitleBar(
                panel.panel_metadata.name,
                closable=panel.panel_metadata.closable,
            )
            self._title_bar.close_requested.connect(lambda: self.setVisible(False))
            self.setTitleBarWidget(self._title_bar)

        # QDockWidget ignores QSS background in favor of the QPalette
        # Window role. Force it to paint its own background so QSS works.
        self.setAutoFillBackground(True)

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
