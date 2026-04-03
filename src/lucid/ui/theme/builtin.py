"""Built-in theme plugins for NCS.

This module contains the default themes that ship with NCS:
- LightThemePlugin: Light/bright theme
- SlateThemePlugin: Neutral gray dark theme
- DarkBlueThemePlugin: Blue-gray dark theme
- IslandsThemePlugin: Modern theme with visually separated components
"""

from __future__ import annotations

from lucid.plugins.theme_plugin import ThemeDefinition, ThemePlugin


class LightThemePlugin(ThemePlugin):
    """Light theme plugin.

    A bright theme with white backgrounds and dark text,
    suitable for well-lit environments.
    """

    @property
    def name(self) -> str:
        return "light"

    @property
    def display_name(self) -> str:
        return "Light"

    @property
    def is_dark(self) -> bool:
        return False

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            primary="#2563eb",
            secondary="#7c3aed",
            success="#16a34a",
            warning="#d97706",
            error="#dc2626",
            info="#0891b2",
            background="#ffffff",
            surface="#f3f4f6",
            text="#1f2937",
            text_secondary="#6b7280",
            border="#e5e7eb",
            disconnected="#ffcccc",
        )


class SlateThemePlugin(ThemePlugin):
    """Slate (dark) theme plugin.

    A neutral gray dark theme that reduces eye strain in low-light
    environments. This is the default dark theme.
    """

    @property
    def name(self) -> str:
        return "slate"

    @property
    def display_name(self) -> str:
        return "Slate (Dark)"

    @property
    def is_dark(self) -> bool:
        return True

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            primary="#3b82f6",
            secondary="#8b5cf6",
            success="#22c55e",
            warning="#f59e0b",
            error="#ef4444",
            info="#06b6d4",
            background="#1e1e1e",
            surface="#2d2d2d",
            text="#d4d4d4",
            text_secondary="#808080",
            border="#3e3e3e",
            disconnected="#5c2020",
        )


class DarkBlueThemePlugin(ThemePlugin):
    """Dark Blue theme plugin.

    A blue-gray dark theme with slightly warmer tones than Slate.
    """

    @property
    def name(self) -> str:
        return "darkblue"

    @property
    def display_name(self) -> str:
        return "Dark Blue"

    @property
    def is_dark(self) -> bool:
        return True

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            primary="#3b82f6",
            secondary="#8b5cf6",
            success="#22c55e",
            warning="#f59e0b",
            error="#ef4444",
            info="#06b6d4",
            background="#1f2937",
            surface="#374151",
            text="#f3f4f6",
            text_secondary="#9ca3af",
            border="#4b5563",
            disconnected="#5c2020",
        )


class IslandsThemePlugin(ThemePlugin):
    """Islands theme plugin.

    A modern dark theme inspired by JetBrains' Islands design language.
    UI components appear as distinct "islands" floating on a darker canvas,
    with rounded corners, subtle elevation, and a purple accent color.

    Key design principles:
    - Visual separation: panels float on a darker background
    - Rounded corners on major UI elements
    - Purple accent color for focus and highlights
    - Soft, balanced appearance for reduced eye strain
    """

    @property
    def name(self) -> str:
        return "islands"

    @property
    def display_name(self) -> str:
        return "Islands"

    @property
    def is_dark(self) -> bool:
        return True

    def get_theme_definition(self) -> ThemeDefinition:
        # Color palette inspired by JetBrains Islands theme
        # Islands (surface) are darker, the sea (gaps between panels) is lighter
        return ThemeDefinition(
            # Purple accent - the signature Islands color
            primary="#6B57FF",
            secondary="#8B7FFF",
            # Status colors - slightly muted to match the soft aesthetic
            success="#3FB950",
            warning="#D29922",
            error="#F85149",
            info="#58A6FF",
            # Islands (panels) — darker, the "land"
            background="#18181B",
            surface="#1E1E22",
            # Sea — lighter than islands, visible in gaps between panels
            sea="#27272A",
            # Text colors - high contrast but not pure white
            text="#FAFAFA",
            text_secondary="#A1A1AA",
            # Borders - subtle, creates soft edges
            border="#3F3F46",
            # Connection states
            connected="#3FB950",
            disconnected="#5C2323",
            # CSS overrides for Islands-specific styling
            css_overrides=_ISLANDS_CSS_OVERRIDES,
        )


# Islands theme CSS overrides for rounded corners, shadows, and visual separation
_ISLANDS_CSS_OVERRIDES = """
/* Islands Theme - Visual separation and rounded corners */

/* Main containers get island treatment */
QGroupBox {
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 8px 8px 8px;
    background-color: #27272A;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    background-color: #27272A;
    border-radius: 4px;
}

/* Dock widgets as islands */
QDockWidget {
    titlebar-close-icon: url(close.png);
    titlebar-normal-icon: url(float.png);
}

QDockWidget::title {
    background: #27272A;
    padding: 8px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}

QDockWidget > QWidget {
    background: #27272A;
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
}

/* Tab bar with rounded tabs */
QTabWidget::pane {
    border: 1px solid #3F3F46;
    border-radius: 8px;
    background: #27272A;
    margin-top: -1px;
}

QTabBar::tab {
    background: #18181B;
    border: 1px solid #3F3F46;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 16px;
    margin-right: 2px;
}

QTabBar::tab:selected {
    background: #27272A;
    border-bottom-color: #27272A;
}

QTabBar::tab:hover:!selected {
    background: #1F1F23;
}

/* Rounded inputs */
QLineEdit {
    border-radius: 6px;
    padding: 6px 10px;
    background: #1F1F23;
    border: 1px solid #3F3F46;
}

QLineEdit:focus {
    border-color: #6B57FF;
    background: #27272A;
}

QComboBox {
    border-radius: 6px;
    padding: 6px 10px;
    background: #1F1F23;
    border: 1px solid #3F3F46;
}

QComboBox:focus {
    border-color: #6B57FF;
}

QComboBox::drop-down {
    border: none;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}

QSpinBox, QDoubleSpinBox {
    border-radius: 6px;
    padding: 6px;
    background: #1F1F23;
    border: 1px solid #3F3F46;
}

QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #6B57FF;
}

/* Rounded buttons with subtle hover */
QPushButton {
    background: #27272A;
    border: 1px solid #3F3F46;
    border-radius: 6px;
    padding: 8px 16px;
}

QPushButton:hover {
    background: #3F3F46;
    border-color: #52525B;
}

QPushButton:pressed {
    background: #52525B;
}

/* Primary button with accent color */
QPushButton[primary="true"] {
    background: #6B57FF;
    color: white;
    border: none;
    border-radius: 6px;
}

QPushButton[primary="true"]:hover {
    background: #5B47EF;
}

QPushButton[primary="true"]:pressed {
    background: #4B37DF;
}

/* Scrollbars with rounded handles */
QScrollBar:vertical {
    background: #18181B;
    width: 14px;
    margin: 0;
    border-radius: 7px;
}

QScrollBar::handle:vertical {
    background: #3F3F46;
    min-height: 30px;
    border-radius: 5px;
    margin: 3px;
}

QScrollBar::handle:vertical:hover {
    background: #52525B;
}

QScrollBar:horizontal {
    background: #18181B;
    height: 14px;
    margin: 0;
    border-radius: 7px;
}

QScrollBar::handle:horizontal {
    background: #3F3F46;
    min-width: 30px;
    border-radius: 5px;
    margin: 3px;
}

QScrollBar::handle:horizontal:hover {
    background: #52525B;
}

QScrollBar::add-line, QScrollBar::sub-line {
    border: none;
    background: none;
    height: 0;
    width: 0;
}

QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
}

/* Menu with island styling */
QMenu {
    background-color: #27272A;
    border: 1px solid #3F3F46;
    border-radius: 8px;
    padding: 4px;
}

QMenu::item {
    padding: 8px 24px;
    border-radius: 4px;
    margin: 2px 4px;
}

QMenu::item:selected {
    background-color: #6B57FF;
    color: white;
}

QMenu::separator {
    height: 1px;
    background: #3F3F46;
    margin: 6px 12px;
}

/* Tooltips as small islands */
QToolTip {
    background-color: #27272A;
    color: #FAFAFA;
    border: 1px solid #3F3F46;
    border-radius: 6px;
    padding: 6px 10px;
}

/* Progress bar with rounded ends */
QProgressBar {
    border: none;
    border-radius: 4px;
    text-align: center;
    background: #1F1F23;
    height: 8px;
}

QProgressBar::chunk {
    background: #6B57FF;
    border-radius: 4px;
}

/* Tree/list/table views */
QTreeView, QListView, QTableView {
    border: 1px solid #3F3F46;
    border-radius: 8px;
    background: #1F1F23;
}

QTreeView::item, QListView::item, QTableView::item {
    padding: 4px;
    border-radius: 4px;
}

QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {
    background: #6B57FF;
    color: white;
}

QTreeView::item:hover:!selected, QListView::item:hover:!selected, QTableView::item:hover:!selected {
    background: #27272A;
}

/* Header with subtle styling */
QHeaderView::section {
    background: #27272A;
    border: none;
    border-right: 1px solid #3F3F46;
    border-bottom: 1px solid #3F3F46;
    padding: 8px;
}

QHeaderView::section:first {
    border-top-left-radius: 8px;
}

QHeaderView::section:last {
    border-top-right-radius: 8px;
    border-right: none;
}

/* Splitters */
QSplitter::handle {
    background: #3F3F46;
}

QSplitter::handle:horizontal {
    width: 2px;
}

QSplitter::handle:vertical {
    height: 2px;
}

/* Status bar as subtle footer */
QStatusBar {
    background: #1F1F23;
    border-top: 1px solid #3F3F46;
}

QStatusBar::item {
    border: none;
}
"""
