"""Minimal Working Example: Custom Icon Strip Sidebar with Docked Panels.

Demonstrates a custom sidebar with icons that control visibility/focus
of normally-docked panels. Unlike QtAds auto-hide, the icons remain
visible even when panels are "pinned" (always visible).

Usage:
    python -m examples.icon_strip_sidebar_mwe

Or from the ncs directory:
    ../.venv-linux/bin/python examples/icon_strip_sidebar_mwe.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import qtawesome as qta
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6QtAds import (
    BottomDockWidgetArea,
    CDockManager,
    CDockWidget,
    CenterDockWidgetArea,
    LeftDockWidgetArea,
)

if TYPE_CHECKING:
    pass


@dataclass
class PanelConfig:
    """Configuration for a panel."""

    id: str
    name: str
    icon: str
    color: str  # Background color for demo
    dock_area: str = "left"  # "left" or "bottom"


class IconStripButton(QToolButton):
    """A toggle button for the icon strip sidebar."""

    def __init__(
        self,
        panel_id: str,
        icon: QIcon,
        tooltip: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.panel_id = panel_id
        self.setIcon(icon)
        self.setToolTip(tooltip)
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setIconSize(QSize(20, 20))
        self.setFixedSize(32, 32)

        # Style for checked/unchecked states
        self.setStyleSheet("""
            QToolButton {
                border: none;
                border-radius: 4px;
                padding: 4px;
                background: transparent;
            }
            QToolButton:hover {
                background: #3d3d3d;
            }
            QToolButton:checked {
                background: #0d6efd;
            }
            QToolButton:checked:hover {
                background: #0b5ed7;
            }
        """)


class IconStripSidebar(QFrame):
    """Custom icon strip sidebar that controls docked panels.

    Unlike QtAds auto-hide sidebars, this keeps icons visible
    regardless of whether panels are pinned/visible.
    """

    panel_toggled = Signal(str, bool)  # panel_id, should_show

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, IconStripButton] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the sidebar UI."""
        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setFixedWidth(40)

        # Dark background
        self.setStyleSheet("""
            IconStripSidebar {
                background: #252526;
                border-right: 1px solid #3d3d3d;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)
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
            icon_name: QtAwesome icon name (e.g., "fa5s.bolt").
            tooltip: Tooltip text shown on hover.

        Returns:
            The created button.
        """
        # Create icon
        try:
            icon = qta.icon(icon_name, color="#cccccc")
        except Exception:
            icon = QIcon()

        button = IconStripButton(panel_id, icon, tooltip, self)
        button.toggled.connect(lambda checked: self._on_button_toggled(panel_id, checked))

        self._buttons[panel_id] = button
        self.layout().addWidget(button)

        return button

    def _on_button_toggled(self, panel_id: str, checked: bool) -> None:
        """Handle button toggle."""
        self.panel_toggled.emit(panel_id, checked)

    def set_panel_active(self, panel_id: str, active: bool) -> None:
        """Set the active state of a panel button.

        Args:
            panel_id: Panel identifier.
            active: Whether the panel is active/visible.
        """
        if panel_id in self._buttons:
            # Block signals to avoid recursion
            button = self._buttons[panel_id]
            button.blockSignals(True)
            button.setChecked(active)
            button.blockSignals(False)

    def add_separator(self) -> None:
        """Add a visual separator."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background: #3d3d3d;")
        separator.setFixedHeight(1)
        self.layout().addWidget(separator)

    def add_stretch(self) -> None:
        """Add stretch to push subsequent buttons to bottom."""
        self.layout().addStretch()


class DemoPanel(QWidget):
    """A simple demo panel with a colored background."""

    def __init__(self, name: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {color};")

        layout = QVBoxLayout(self)
        label = QLabel(name)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        layout.addWidget(label)


class MainWindow(QMainWindow):
    """Main window demonstrating custom icon strip sidebar."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Icon Strip Sidebar MWE")
        self.resize(1200, 800)

        # Panel configurations - top icons dock to left, bottom icons dock to bottom
        self._panel_configs_top = [
            PanelConfig("explorer", "Explorer", "fa5s.folder", "#1e3a5f", "left"),
            PanelConfig("search", "Search", "fa5s.search", "#3d1e5f", "left"),
            PanelConfig("git", "Git", "fa5s.code-branch", "#1e5f3a", "left"),
        ]
        self._panel_configs_bottom = [
            PanelConfig("terminal", "Terminal", "fa5s.terminal", "#2d2d2d", "bottom"),
            PanelConfig("problems", "Problems", "fa5s.exclamation-triangle", "#5f1e1e", "bottom"),
            PanelConfig("output", "Output", "fa5s.stream", "#1e5f5f", "bottom"),
        ]

        self._dock_widgets: dict[str, CDockWidget] = {}
        self._panel_areas: dict[str, str] = {}  # panel_id -> "left" or "bottom"
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the main UI."""
        # Central widget with horizontal layout
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Left icon strip sidebar
        self._sidebar = IconStripSidebar()
        self._sidebar.panel_toggled.connect(self._on_panel_toggled)
        layout.addWidget(self._sidebar)

        # CDockManager for the main content area
        CDockManager.setConfigFlag(CDockManager.OpaqueSplitterResize, True)
        CDockManager.setConfigFlag(CDockManager.FocusHighlighting, True)
        CDockManager.setConfigFlag(CDockManager.DockAreaHasTabsMenuButton, False)
        CDockManager.setConfigFlag(CDockManager.HideSingleCentralWidgetTitleBar, True)

        self._dock_manager = CDockManager()
        layout.addWidget(self._dock_manager)

        # Apply dark theme to dock manager
        self._dock_manager.setStyleSheet("""
            ads--CDockContainerWidget {
                background: #1e1e1e;
            }
            ads--CDockAreaWidget {
                background: #1e1e1e;
                border: 1px solid #3d3d3d;
            }
            ads--CDockWidgetTab {
                background: #2d2d2d;
                border: 1px solid #3d3d3d;
                padding: 4px 12px;
            }
            ads--CDockWidgetTab[activeTab="true"] {
                background: #1e1e1e;
            }
            ads--CDockAreaTitleBar {
                background: #2d2d2d;
                border-bottom: 1px solid #3d3d3d;
            }
            ads--CDockSplitter::handle {
                background: #3d3d3d;
            }
        """)

        # Create center area first (so we can dock panels relative to it)
        self._center_area = self._create_center_area()

        # Create panels and add to sidebar
        self._create_panels()

    def _create_panel(self, config: PanelConfig) -> CDockWidget:
        """Create a single dock widget for a panel config.

        Args:
            config: Panel configuration.

        Returns:
            The created CDockWidget.
        """
        # Create the panel widget
        panel = DemoPanel(config.name, config.color)

        # Create dock widget
        dock_widget = CDockWidget(config.name)
        dock_widget.setWidget(panel)
        dock_widget.setFeature(CDockWidget.DockWidgetDeleteOnClose, False)
        dock_widget.setFeature(CDockWidget.NoTab, True)  # Hide tab since only one panel per area

        # Set icon
        try:
            icon = qta.icon(config.icon, color="#cccccc")
            dock_widget.setIcon(icon)
        except Exception:
            pass

        # Store reference
        self._dock_widgets[config.id] = dock_widget

        # Connect visibility signal to update sidebar button
        dock_widget.viewToggled.connect(
            lambda visible, pid=config.id: self._on_dock_visibility_changed(pid, visible)
        )

        return dock_widget

    def _create_panels(self) -> None:
        """Create dock widgets for all panel configs."""
        # Top sidebar icons -> dock to left of center
        for config in self._panel_configs_top:
            dock_widget = self._create_panel(config)

            # Add to left area
            self._dock_manager.addDockWidget(LeftDockWidgetArea, dock_widget)

            # Track which area this panel belongs to
            self._panel_areas[config.id] = "left"

            # Add button to sidebar (top section)
            self._sidebar.add_panel_button(config.id, config.icon, config.name)

            # Initially hide
            dock_widget.toggleView(False)

        # Add stretch to push bottom icons down
        self._sidebar.add_stretch()

        # Bottom sidebar icons -> dock to bottom of center
        for config in self._panel_configs_bottom:
            dock_widget = self._create_panel(config)

            # Add to bottom area
            self._dock_manager.addDockWidget(BottomDockWidgetArea, dock_widget)

            # Track which area this panel belongs to
            self._panel_areas[config.id] = "bottom"

            # Add button to sidebar (bottom section)
            self._sidebar.add_panel_button(config.id, config.icon, config.name)

            # Initially hide
            dock_widget.toggleView(False)

    def _create_center_area(self):
        """Create a center editor-like area.

        Returns:
            The dock area widget containing the editor.
        """
        editor = QWidget()
        editor.setStyleSheet("background: #1e1e1e;")
        layout = QVBoxLayout(editor)

        label = QLabel("Main Editor Area\n\nClick sidebar icons to toggle panels")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #808080; font-size: 16px;")
        layout.addWidget(label)

        dock_widget = CDockWidget("Editor")
        dock_widget.setWidget(editor)
        dock_widget.setFeature(CDockWidget.DockWidgetClosable, False)

        # Add to center and return the dock area
        return self._dock_manager.addDockWidget(CenterDockWidgetArea, dock_widget)

    def _on_panel_toggled(self, panel_id: str, should_show: bool) -> None:
        """Handle sidebar button toggle.

        Only one panel per area (left/bottom) can be visible at a time.

        Args:
            panel_id: The panel that was toggled.
            should_show: Whether to show or hide the panel.
        """
        if panel_id not in self._dock_widgets:
            return

        dock_widget = self._dock_widgets[panel_id]
        panel_area = self._panel_areas.get(panel_id)

        if should_show:
            # Hide other panels in the same area first
            for other_id, other_area in self._panel_areas.items():
                if other_id != panel_id and other_area == panel_area:
                    other_widget = self._dock_widgets.get(other_id)
                    if other_widget and not other_widget.isClosed():
                        other_widget.toggleView(False)

            # Show and raise the panel
            dock_widget.toggleView(True)
            dock_widget.raise_()
        else:
            # Hide the panel
            dock_widget.toggleView(False)

    def _on_dock_visibility_changed(self, panel_id: str, visible: bool) -> None:
        """Handle dock widget visibility change (e.g., closed via X button).

        Args:
            panel_id: The panel whose visibility changed.
            visible: Whether the panel is now visible.
        """
        self._sidebar.set_panel_active(panel_id, visible)


def main() -> None:
    """Run the example."""
    app = QApplication(sys.argv)

    # Dark theme for the app
    app.setStyleSheet("""
        QMainWindow {
            background: #1e1e1e;
        }
        QToolTip {
            background: #2d2d2d;
            color: #cccccc;
            border: 1px solid #3d3d3d;
            padding: 4px;
        }
    """)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
