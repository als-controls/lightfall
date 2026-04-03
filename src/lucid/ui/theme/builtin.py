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

    # --- Islands color constants (used in both definition and CSS) ---
    _BG = "#18181B"       # background (deepest)
    _ISLAND = "#1E1E22"   # surface / island panels
    _SEA = "#27272A"      # sea (gaps, title bars, menubar)
    _INPUT = "#1F1F23"    # input fields
    _BORDER = "#3F3F46"
    _BORDER_HI = "#52525B"
    _ACCENT = "#6B57FF"
    _ACCENT_HOVER = "#5B47EF"
    _ACCENT_PRESS = "#4B37DF"
    _TEXT = "#FAFAFA"
    _TEXT2 = "#A1A1AA"

    def get_theme_definition(self) -> ThemeDefinition:
        # Color palette inspired by JetBrains Islands theme
        # Islands (surface) are darker, the sea (gaps between panels) is lighter
        c = self  # shortcut to class attrs
        return ThemeDefinition(
            primary=c._ACCENT,
            secondary="#8B7FFF",
            success="#3FB950",
            warning="#D29922",
            error="#F85149",
            info="#58A6FF",
            background=c._BG,
            surface=c._ISLAND,
            sea=c._SEA,
            text=c._TEXT,
            text_secondary=c._TEXT2,
            border=c._BORDER,
            connected="#3FB950",
            disconnected="#5C2323",
            css_overrides=_build_islands_css(c),
        )


def _build_islands_css(c) -> str:
    """Build Islands CSS overrides from the class color constants."""
    return f"""
/* Islands Theme - Visual separation and rounded corners */

/* --------------------------------------------------------------------------
   Menu bar — sea color, sits above the islands
   -------------------------------------------------------------------------- */
QMenuBar {{
    background: {c._SEA};
    color: {c._TEXT};
    border: none;
    padding: 2px 4px;
    spacing: 2px;
}}

QMenuBar::item {{
    background: transparent;
    padding: 4px 8px;
    border-radius: 6px;
}}

QMenuBar::item:selected {{
    background: {c._BORDER};
}}

/* --------------------------------------------------------------------------
   Menus — island-styled popups
   -------------------------------------------------------------------------- */
QMenu {{
    background-color: {c._SEA};
    border: 1px solid {c._BORDER};
    border-radius: 8px;
    padding: 4px;
}}

QMenu::item {{
    padding: 8px 24px;
    border-radius: 4px;
    margin: 2px 4px;
}}

QMenu::item:selected {{
    background-color: {c._ACCENT};
    color: white;
}}

QMenu::separator {{
    height: 1px;
    background: {c._BORDER};
    margin: 6px 12px;
}}

/* Fix: Qt renders a white corner artifact on rounded menus.
   Force the menu viewport (scroll area) to be transparent. */
QMenu QWidget {{
    background: transparent;
}}

/* --------------------------------------------------------------------------
   Group boxes — island containers
   -------------------------------------------------------------------------- */
QGroupBox {{
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 8px 8px 8px;
    background-color: {c._SEA};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    background-color: {c._SEA};
    border-radius: 4px;
}}

/* --------------------------------------------------------------------------
   Tabs
   -------------------------------------------------------------------------- */
QTabWidget::pane {{
    border: 1px solid {c._BORDER};
    border-radius: 8px;
    background: {c._SEA};
    margin-top: -1px;
}}

QTabBar::tab {{
    background: {c._BG};
    border: 1px solid {c._BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 16px;
    margin-right: 2px;
}}

QTabBar::tab:selected {{
    background: {c._SEA};
    border-bottom-color: {c._SEA};
}}

QTabBar::tab:hover:!selected {{
    background: {c._INPUT};
}}

/* --------------------------------------------------------------------------
   Inputs
   -------------------------------------------------------------------------- */
QLineEdit {{
    border-radius: 6px;
    padding: 6px 10px;
    background: {c._INPUT};
    border: 1px solid {c._BORDER};
}}

QLineEdit:focus {{
    border-color: {c._ACCENT};
    background: {c._SEA};
}}

QComboBox {{
    border-radius: 6px;
    padding: 6px 10px;
    background: {c._INPUT};
    border: 1px solid {c._BORDER};
}}

QComboBox:focus {{
    border-color: {c._ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}}

QSpinBox, QDoubleSpinBox {{
    border-radius: 6px;
    padding: 6px;
    background: {c._INPUT};
    border: 1px solid {c._BORDER};
}}

QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {c._ACCENT};
}}

/* --------------------------------------------------------------------------
   Buttons
   -------------------------------------------------------------------------- */
QPushButton {{
    background: {c._SEA};
    border: 1px solid {c._BORDER};
    border-radius: 6px;
    padding: 8px 16px;
}}

QPushButton:hover {{
    background: {c._BORDER};
    border-color: {c._BORDER_HI};
}}

QPushButton:pressed {{
    background: {c._BORDER_HI};
}}

QPushButton[primary="true"] {{
    background: {c._ACCENT};
    color: white;
    border: none;
    border-radius: 6px;
}}

QPushButton[primary="true"]:hover {{
    background: {c._ACCENT_HOVER};
}}

QPushButton[primary="true"]:pressed {{
    background: {c._ACCENT_PRESS};
}}

/* --------------------------------------------------------------------------
   Scrollbars
   -------------------------------------------------------------------------- */
QScrollBar:vertical {{
    background: {c._BG};
    width: 14px;
    margin: 0;
    border-radius: 7px;
}}

QScrollBar::handle:vertical {{
    background: {c._BORDER};
    min-height: 30px;
    border-radius: 5px;
    margin: 3px;
}}

QScrollBar::handle:vertical:hover {{
    background: {c._BORDER_HI};
}}

QScrollBar:horizontal {{
    background: {c._BG};
    height: 14px;
    margin: 0;
    border-radius: 7px;
}}

QScrollBar::handle:horizontal {{
    background: {c._BORDER};
    min-width: 30px;
    border-radius: 5px;
    margin: 3px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {c._BORDER_HI};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    border: none;
    background: none;
    height: 0;
    width: 0;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* --------------------------------------------------------------------------
   Tooltips
   -------------------------------------------------------------------------- */
QToolTip {{
    background-color: {c._SEA};
    color: {c._TEXT};
    border: 1px solid {c._BORDER};
    border-radius: 6px;
    padding: 6px 10px;
}}

/* --------------------------------------------------------------------------
   Progress bar
   -------------------------------------------------------------------------- */
QProgressBar {{
    border: none;
    border-radius: 4px;
    text-align: center;
    background: {c._INPUT};
    height: 8px;
}}

QProgressBar::chunk {{
    background: {c._ACCENT};
    border-radius: 4px;
}}

/* --------------------------------------------------------------------------
   Tree / List / Table views
   -------------------------------------------------------------------------- */
QTreeView, QListView, QTableView {{
    border: 1px solid {c._BORDER};
    border-radius: 8px;
    background: {c._INPUT};
}}

QTreeView::item, QListView::item, QTableView::item {{
    padding: 4px;
    border-radius: 4px;
}}

QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
    background: {c._ACCENT};
    color: white;
}}

QTreeView::item:hover:!selected, QListView::item:hover:!selected, QTableView::item:hover:!selected {{
    background: {c._SEA};
}}

/* --------------------------------------------------------------------------
   Headers
   -------------------------------------------------------------------------- */
QHeaderView::section {{
    background: {c._SEA};
    border: none;
    border-right: 1px solid {c._BORDER};
    border-bottom: 1px solid {c._BORDER};
    padding: 8px;
}}

QHeaderView::section:first {{
    border-top-left-radius: 8px;
}}

QHeaderView::section:last {{
    border-top-right-radius: 8px;
    border-right: none;
}}

/* --------------------------------------------------------------------------
   Splitters
   -------------------------------------------------------------------------- */
QSplitter::handle {{
    background: {c._BORDER};
}}

QSplitter::handle:horizontal {{
    width: 2px;
}}

QSplitter::handle:vertical {{
    height: 2px;
}}

/* --------------------------------------------------------------------------
   Status bar
   -------------------------------------------------------------------------- */
QStatusBar {{
    background: {c._SEA};
    border-top: none;
}}

QStatusBar::item {{
    border: none;
}}
"""
