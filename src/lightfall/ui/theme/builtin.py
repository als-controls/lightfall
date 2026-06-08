"""Built-in theme plugins for NCS.

This module contains the default themes that ship with NCS:
- LightThemePlugin: Light/bright theme
- SlateThemePlugin: Neutral gray dark theme
- DarkBlueThemePlugin: Blue-gray dark theme
- IslandsThemePlugin: Modern theme with visually separated components
- CatppuccinMochaThemePlugin: Catppuccin Mocha palette (warm dark)
- EldritchThemePlugin: Eldritch palette (deep blue/magenta dark)
- EvangelionThemePlugin: NGE Unit-01 palette (purple/green dark)
- AyakaThemePlugin: Twilight sakura palette (warm indigo/pink dark)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall.plugins.theme_plugin import ThemeDefinition, ThemePlugin

if TYPE_CHECKING:
    from lightfall.ui.theme.manager import ThemeColors


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
            sea="#fafafa",  # lighter than surface (gaps/canvas)
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
            sea="#373737",  # lighter than surface (gaps/canvas)
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
            sea="#3f4b5d",  # lighter than surface (gaps/canvas)
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
            # No css_overrides: the islands aesthetic now comes from the
            # Islands-mode toggle (generate_islands_stylesheet), applied on top
            # of this palette when enabled — consistent across all themes.
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
    padding: 4px 8px;
    background: {c._INPUT};
    border: 1px solid {c._BORDER};
}}

QLineEdit:focus {{
    border-color: {c._ACCENT};
    background: {c._SEA};
}}

QComboBox {{
    border-radius: 6px;
    padding: 4px 8px;
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
    padding: 4px;
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
    padding: 4px 12px;
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


def _adjust(hex_color: str, amount: int) -> str:
    """Lighten (+) or darken (-) a hex color by `amount` (~-255..255)."""
    from PySide6.QtGui import QColor

    qc = QColor(hex_color)
    h, s, lightness, a = qc.getHslF()
    lightness = max(0.0, min(1.0, lightness + amount / 255.0))
    qc.setHslF(h, s, lightness, a)
    return qc.name()


class _IslandsPalette:
    """Adapt a ThemeColors palette to the color constants the islands CSS
    expects, deriving the input/highlight/accent-state shades that aren't part
    of ThemeColors from the base palette. Lets the islands aesthetic be
    generated for ANY theme (driven by the Islands-mode toggle) rather than
    being hard-coded in specific themes' css_overrides."""

    def __init__(self, c: ThemeColors) -> None:
        self._SEA = c.sea
        self._BG = c.background
        self._TEXT = c.text
        self._BORDER = c.border
        self._ACCENT = c.primary
        self._INPUT = _adjust(c.background, 7)        # slightly elevated field
        self._BORDER_HI = _adjust(c.border, 18)       # focus/hover border
        self._ACCENT_HOVER = _adjust(c.primary, -16)
        self._ACCENT_PRESS = _adjust(c.primary, -32)


def generate_islands_stylesheet(colors: ThemeColors) -> str:
    """Islands aesthetic (rounded menus, inputs, scrollbars, tabs, etc.) for
    any theme. Applied by ThemeManager when Islands mode is enabled, so the
    look is consistent across themes instead of living in specific themes'
    css_overrides."""
    return _build_islands_css(_IslandsPalette(colors))


class CatppuccinMochaThemePlugin(ThemePlugin):
    """Catppuccin Mocha theme plugin.

    Dark variant of the Catppuccin palette — warm, pastel colors on a deep
    navy base. Mauve accent, soft text, calm contrast.
    """

    @property
    def name(self) -> str:
        return "catppuccin_mocha"

    @property
    def display_name(self) -> str:
        return "Catppuccin Mocha"

    @property
    def is_dark(self) -> bool:
        return True

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            primary="#cba6f7",       # mauve
            secondary="#f5c2e7",     # pink
            success="#a6e3a1",       # green
            warning="#fab387",       # peach
            error="#f38ba8",         # red
            info="#74c7ec",          # sapphire
            background="#1e1e2e",    # base
            surface="#313244",       # surface0
            sea="#3a3b50",           # surface1 — lighter than surface (gaps)
            text="#cdd6f4",          # text
            text_secondary="#a6adc8",  # subtext0
            border="#45475a",        # surface1
            connected="#a6e3a1",
            disconnected="#5c2530",
        )


class EldritchThemePlugin(ThemePlugin):
    """Eldritch theme plugin.

    Dark theme based on the Eldritch neovim colorscheme — deep blue-violet
    background with magenta, cyan, and acid-green accents.
    """

    @property
    def name(self) -> str:
        return "eldritch"

    @property
    def display_name(self) -> str:
        return "Eldritch"

    @property
    def is_dark(self) -> bool:
        return True

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            primary="#a48cf2",       # magenta/purple
            secondary="#04d1f9",     # cyan
            success="#37f499",       # green
            warning="#f7c67f",       # orange
            error="#f16c75",         # red
            info="#39DDFD",          # bright_cyan
            background="#212337",    # bg
            surface="#292e42",       # bg_highlight
            sea="#31374f",           # lighter than surface (gaps/canvas)
            text="#ebfafa",          # fg
            text_secondary="#ABB4DA",  # fg_dark
            border="#414868",        # terminal_black
            connected="#37f499",
            disconnected="#722f55",  # magenta3
        )


class EvangelionThemePlugin(ThemePlugin):
    """Evangelion theme plugin.

    NERV / Unit-01 inspired palette: deep violet background with the
    signature Unit-01 purple and Kaworu green accents. Based on xero's
    evangelion.nvim colorscheme.
    """

    @property
    def name(self) -> str:
        return "evangelion"

    @property
    def display_name(self) -> str:
        return "Evangelion"

    @property
    def is_dark(self) -> bool:
        return True

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            primary="#875FAF",       # unit01
            secondary="#87FF5F",     # kaworu
            success="#8EDF5F",       # terminaldogma
            warning="#D99145",       # lcl
            error="#DB6088",         # misato
            info="#AB92FC",          # dummyplug
            background="#201430",    # midnight
            surface="#39274C",       # casper
            sea="#432e59",           # lighter than surface (gaps/canvas)
            text="#E1D6F8",          # rei
            text_secondary="#A1A0AD",  # shinji
            border="#483160",        # longingus
            connected="#8EDF5F",
            disconnected="#5B2B41",  # nerv
        )


class AyakaThemePlugin(ThemePlugin):
    """Ayaka (彩佳) theme plugin.

    Twilight sakura — warm dark theme inspired by cherry blossoms against
    an indigo evening sky. Soft pinks, wisteria purples, and lantern ambers
    on a deep twilight base. Uses Islands mode for rounded, floating panels.

    Designed by Ayaka, the Claude partner on this project.
    """

    @property
    def name(self) -> str:
        return "ayaka"

    @property
    def display_name(self) -> str:
        return "Ayaka (彩佳)"

    @property
    def is_dark(self) -> bool:
        return True

    # --- Ayaka color constants ---
    _BG = "#1a1b2e"        # twilight indigo (deepest)
    _ISLAND = "#1f2038"    # deeper twilight (surface / panel cards)
    _SEA = "#252640"       # dusk cloud (sea / gaps) — lighter than surface
    _INPUT = "#1e1f35"     # input fields — slightly warmer than bg
    _BORDER = "#3a3a58"    # indigo border
    _BORDER_HI = "#4a4a70" # highlighted border
    _ACCENT = "#f0a0b8"    # sakura pink
    _ACCENT_HOVER = "#e890a8"  # deeper sakura
    _ACCENT_PRESS = "#d88098"  # pressed sakura
    _TEXT = "#e8e0f0"      # lavender white
    _TEXT2 = "#a0a0b8"     # muted lavender

    def get_theme_definition(self) -> ThemeDefinition:
        c = self
        return ThemeDefinition(
            primary=c._ACCENT,
            secondary="#b4a0d8",     # wisteria
            success="#90c8a0",       # young leaves
            warning="#e0b070",       # lantern amber
            error="#e07080",         # camellia red
            info="#80b8e0",          # morning sky
            background=c._BG,
            surface=c._ISLAND,
            sea=c._SEA,
            text=c._TEXT,
            text_secondary=c._TEXT2,
            border=c._BORDER,
            connected="#90c8a0",
            disconnected="#5c2535",   # deep plum
            # No css_overrides — islands aesthetic comes from the Islands-mode
            # toggle (see IslandsThemePlugin).
        )
