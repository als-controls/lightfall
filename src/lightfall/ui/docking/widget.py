"""PanelDockWidget - QDockWidget specialized for Lightfall panels.

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
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


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
        from lightfall.ui.theme import ThemeManager
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

    Layout, left to right:
        [title] ... [panel action buttons] | [expand][redock?][minimize]

    Signals:
        close_requested: Minimize button clicked (hides the panel).
        expand_requested: Expand button clicked (theater mode).
        redock_requested: Redock button clicked (return floating panel).
    """

    close_requested = Signal()
    expand_requested = Signal()
    redock_requested = Signal()

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
            closable: Whether to show the minimize button.
        """
        super().__init__(parent)
        self.setObjectName("PanelTitleBar")
        self.setFixedHeight(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 4)
        layout.setSpacing(4)

        # Title label
        label = QLabel(title)
        label.setObjectName("PanelTitleLabel")
        layout.addWidget(label)
        layout.addStretch()

        # Panel-contributed action buttons live in their own sub-layout
        # so set_actions() can rebuild them without touching the window
        # buttons.
        self._actions_layout = QHBoxLayout()
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_layout.setSpacing(4)
        layout.addLayout(self._actions_layout)

        # Separator between action buttons and window buttons
        self._separator = QFrame()
        self._separator.setObjectName("PanelTitleSeparator")
        self._separator.setFrameShape(QFrame.Shape.VLine)
        self._separator.setFixedHeight(16)
        self._separator.setVisible(False)
        layout.addWidget(self._separator)

        icon_color = self._icon_color()

        # Expand (theater mode) — hidden while floating
        self._expand_btn = self._make_button(
            "mdi6.arrow-expand-all", "Expand to fill window", icon_color
        )
        self._expand_btn.clicked.connect(self.expand_requested.emit)
        layout.addWidget(self._expand_btn)

        # Redock — only visible while floating
        self._redock_btn = self._make_button(
            "mdi6.dock-window", "Return to docked position", icon_color
        )
        self._redock_btn.clicked.connect(self.redock_requested.emit)
        self._redock_btn.setVisible(False)
        layout.addWidget(self._redock_btn)

        # Minimize (hides the panel)
        if closable:
            self._minimize_btn = self._make_button(
                "mdi6.window-minimize", "Hide panel", icon_color
            )
            self._minimize_btn.clicked.connect(self.close_requested.emit)
            layout.addWidget(self._minimize_btn)

    @staticmethod
    def _icon_color() -> str:
        """Theme secondary text color for title bar button icons."""
        try:
            from lightfall.ui.theme import ThemeManager

            return ThemeManager.get_instance().colors.text_secondary
        except Exception:
            return "#808080"

    def _make_button(
        self, icon_name: str, tooltip: str, icon_color: str
    ) -> QToolButton:
        """Create a 20x20 icon-only title bar button."""
        btn = QToolButton()
        btn.setObjectName("PanelTitleButton")
        btn.setFixedSize(20, 20)
        btn.setCursor(Qt.CursorShape.ArrowCursor)
        btn.setToolTip(tooltip)
        try:
            btn.setIcon(qta.icon(icon_name, color=icon_color))
        except Exception:
            btn.setText("?")
        return btn

    def set_actions(self, actions: list[QAction]) -> None:
        """Rebuild the panel-contributed action buttons.

        Args:
            actions: Actions to render as icon-only buttons (in order).
        """
        while self._actions_layout.count():
            item = self._actions_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for action in actions:
            btn = QToolButton()
            btn.setObjectName("PanelTitleButton")
            btn.setFixedSize(20, 20)
            btn.setCursor(Qt.CursorShape.ArrowCursor)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setDefaultAction(action)
            self._actions_layout.addWidget(btn)
        self._separator.setVisible(bool(actions))

    def set_floating(self, floating: bool) -> None:
        """Swap expand/redock buttons based on floating state.

        Args:
            floating: Whether the dock widget is currently floating.
        """
        self._redock_btn.setVisible(floating)
        self._expand_btn.setVisible(not floating)


class PanelDockWidget(QDockWidget):
    """QDockWidget specialized for Lightfall panels.

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
