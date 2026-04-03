"""PanelDockWidget - QDockWidget specialized for LUCID panels.

Wraps panels in QDockWidget with proper icon, title, and feature
configuration based on panel metadata.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import qtawesome as qta
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
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
        icon_color = "#d4d4d4"  # Default to light gray

    # Try FontAwesome icon first
    try:
        # Support both "bolt" and "fa5s.bolt" formats
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

    # Fall back to file path
    return QIcon(icon_name)


class PanelTitleBar(QFrame):
    """Custom title bar for side panels.

    Shows the panel name in a clean header style with close button.
    Supports drag-to-undock functionality.

    Signals:
        close_requested: Emitted when the close button is clicked.
        drag_started: Emitted when a drag operation starts (for undocking).
    """

    close_requested = Signal()
    drag_started = Signal(QPoint)  # Global position where drag started

    # Minimum drag distance before initiating undock
    DRAG_THRESHOLD = 10

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
        self._drag_start_pos: QPoint | None = None
        self._closable = closable

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
            # Set close icon
            try:
                from lucid.ui.theme import ThemeManager
                theme_mgr = ThemeManager.get_instance()
                icon_color = theme_mgr.colors.text_secondary
            except Exception:
                icon_color = "#808080"
            try:
                import qtawesome as qta
                self._close_btn.setIcon(qta.icon("mdi.close", color=icon_color))
            except Exception:
                self._close_btn.setText("x")
            layout.addWidget(self._close_btn)

        # Enable mouse tracking for drag detection
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press for drag initiation."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move for drag detection."""
        if self._drag_start_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_start_pos
            if delta.manhattanLength() >= self.DRAG_THRESHOLD:
                self.drag_started.emit(self._drag_start_pos)
                self._drag_start_pos = None
                self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Handle mouse release."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)


class PanelContainer(QWidget):
    """Container widget that adds a title bar above the panel content.

    Used for side panels to show title when the native QDockWidget
    title bar is hidden.
    """

    def __init__(self, panel: BasePanel, parent: QWidget | None = None) -> None:
        """Initialize the container.

        Args:
            panel: The panel to wrap.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._panel = panel
        self._dock_widget: QDockWidget | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Add title bar with close button based on panel metadata
        self._title_bar = PanelTitleBar(
            panel.panel_metadata.name,
            closable=panel.panel_metadata.closable,
        )
        self._title_bar.close_requested.connect(self._on_close_requested)
        self._title_bar.drag_started.connect(self._on_drag_started)
        layout.addWidget(self._title_bar)

        # Add panel content
        layout.addWidget(panel)

    def set_dock_widget(self, dock_widget: QDockWidget) -> None:
        """Set the parent dock widget reference.

        Args:
            dock_widget: The QDockWidget that contains this container.
        """
        self._dock_widget = dock_widget

    def _on_close_requested(self) -> None:
        """Handle close button click."""
        if self._dock_widget is not None:
            self._dock_widget.setVisible(False)

    def _on_drag_started(self, global_pos: QPoint) -> None:
        """Handle drag start for undocking.

        Args:
            global_pos: The global position where the drag started.
        """
        if self._dock_widget is not None:
            self._dock_widget.setFloating(True)
            if self._dock_widget.isFloating():
                self._dock_widget.move(global_pos.x() - 50, global_pos.y() - 14)

    @property
    def panel(self) -> BasePanel:
        """Get the wrapped panel."""
        return self._panel


class PanelDockWidget(QDockWidget):
    """QDockWidget specialized for LUCID panels.

    Wraps a BasePanel in a QDockWidget with:
    - Icon from panel metadata
    - Title from panel metadata
    - Feature flags based on closable setting
    - Custom title bar for side panels
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
            use_custom_title_bar: If True, wrap panel in container with
                custom title bar and hide native QDockWidget title bar.
        """
        super().__init__(panel.panel_metadata.name, parent)
        self._panel = panel
        self._container: PanelContainer | None = None

        # Set object name for state persistence
        self.setObjectName(f"dock_{panel.panel_metadata.id}")

        # Set content widget - either wrapped or direct
        if use_custom_title_bar:
            self._container = PanelContainer(panel)
            self._container.set_dock_widget(self)
            self.setWidget(self._container)
            # Hide native title bar — our PanelTitleBar replaces it
            self.setTitleBarWidget(QWidget())
        else:
            self.setWidget(panel)

        # Configure features based on metadata
        features = QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        if panel.panel_metadata.closable:
            features |= QDockWidget.DockWidgetFeature.DockWidgetClosable
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
