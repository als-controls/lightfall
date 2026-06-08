"""Tests for the docking stylesheet generator (islands vs non-islands).

``generate_docking_stylesheet`` is a pure function (ThemeColors -> CSS string),
so these tests assert on the generated CSS without needing a QApplication.
"""

from __future__ import annotations

import pytest

from lightfall.ui.docking.theme import (
    RADIUS,
    _is_islands_mode,
    generate_docking_stylesheet,
)
from lightfall.ui.theme.manager import ThemeColors


@pytest.fixture
def islands_colors() -> ThemeColors:
    """An islands-mode palette (sea distinct from background)."""
    return ThemeColors(
        primary="#f0a0b8",
        surface="#252640",
        background="#1a1b2e",
        sea="#1f2038",
        text="#e8e0f0",
        text_secondary="#a0a0b8",
        border="#3a3a58",
    )


@pytest.fixture
def flat_colors() -> ThemeColors:
    """A non-islands palette (no distinct sea -> falls back to background)."""
    return ThemeColors(
        primary="#875FAF",
        surface="#39274C",
        background="#201430",
        sea="",  # __post_init__ fills this with background -> non-islands
        text="#E1D6F8",
        text_secondary="#A1A0AD",
        border="#483160",
    )


def test_fixtures_select_expected_modes(islands_colors, flat_colors):
    assert _is_islands_mode(islands_colors) is True
    assert _is_islands_mode(flat_colors) is False


def test_selected_sidebar_button_is_surface_in_islands(islands_colors):
    """Selected (checked) sidebar tool buttons use the Surface color, not the
    loud primary accent, so the active button reads as part of the island."""
    css = generate_docking_stylesheet(islands_colors)
    assert (
        "#IconStripSidebar QToolButton:checked {\n"
        f"    background: {islands_colors.surface};"
    ) in css
    # The primary accent should NOT be the checked background in islands mode.
    assert (
        "#IconStripSidebar QToolButton:checked {\n"
        f"    background: {islands_colors.primary};"
    ) not in css


def test_selected_sidebar_button_stays_primary_when_flat(flat_colors):
    """Non-islands themes keep the existing primary highlight (unchanged)."""
    css = generate_docking_stylesheet(flat_colors)
    assert (
        "#IconStripSidebar QToolButton:checked {\n"
        f"    background: {flat_colors.primary};"
    ) in css


def test_panel_background_is_surface(islands_colors):
    """The panel card is surface; the QDockWidget canvas behind it is sea, so
    the card's rounded corners read as a floating surface island. Shells use
    background-color (not the `background` shorthand) so they paint reliably
    when docked."""
    css = generate_docking_stylesheet(islands_colors)
    # Dock canvas behind the card = sea.
    assert (
        "QDockWidget {\n"
        f"    background-color: {islands_colors.sea};"
    ) in css
    # Panel card body (proxy) = surface.
    assert (
        "QDockWidget > QWidget {\n"
        f"    background-color: {islands_colors.surface};"
    ) in css


def test_panel_scroll_subtree_transparent_in_islands(islands_colors):
    """A QScrollArea (viewport + scrolled child) autofills the Window role
    (= sea) and, as opaque squares, paints sea and squares off the rounded
    corners of the surface behind it. Islands mode makes the whole scroll
    subtree transparent (global, so nested scroll areas like the LogbookPanel
    are covered) so the rounded surface shows through."""
    css = generate_docking_stylesheet(islands_colors)
    assert (
        "QScrollArea,\n"
        "QScrollArea > QWidget,\n"
        "QScrollArea > QWidget > QWidget,\n"
        "QDockWidget QFrame,\n"
        "#InnerDockWindow QFrame,\n"
        "#EntryListWidget {\n"
        "    background-color: rgba(0, 0, 0, 0);"
    ) in css


def test_dock_panel_subtree_is_surface_in_islands(islands_colors):
    """Docked panel = rounded surface card. The card body (first QWidget under
    the proxy) paints surface with bottom rounding (painted directly, since a
    docked QDockWidget paints nothing and the TheaterProxy QStackedWidget
    doesn't paint behind its page); its content is transparent so the rounded
    surface + sea-revealing corners show. Descendant combinator (not '>') —
    Qt's '>' does not match QStackedWidget pages."""
    css = generate_docking_stylesheet(islands_colors)
    assert (
        "#TheaterProxy QWidget {\n"
        f"    background-color: {islands_colors.surface};\n"
        f"    border-bottom-left-radius: {RADIUS}px;\n"
        f"    border-bottom-right-radius: {RADIUS}px;"
    ) in css
    # Card content (deeper widgets) transparent so the rounded surface shows.
    assert (
        "#TheaterProxy QWidget QWidget {\n"
        "    background-color: rgba(0, 0, 0, 0);"
    ) in css
    # The direct-child form silently misses the stacked panel; guard against
    # a well-meaning "simplification" back to it.
    assert "#TheaterProxy > QWidget {" not in css
    # Title bar is a QFrame (a shell, not container) — re-asserted surface with
    # a selector that wins over the container QFrame transparency.
    assert (
        "QDockWidget #PanelTitleBar {\n"
        f"    background-color: {islands_colors.surface};"
    ) in css


def test_dock_panel_subtree_rule_absent_when_flat(flat_colors):
    css = generate_docking_stylesheet(flat_colors)
    assert "#TheaterProxy QWidget" not in css


def test_island_widget_polish(islands_colors):
    """Islands themes: selected tab = surface, stacked widget = surface,
    push buttons flat/sea/borderless."""
    css = generate_docking_stylesheet(islands_colors)
    # Idle tabs: surface, borderless, rounded-rect (button-like).
    assert (
        f"QTabBar::tab {{\n    background-color: {islands_colors.surface};\n"
        f"    color: {islands_colors.text};\n    border: none;"
    ) in css
    # Active tab: primary with a border.
    assert (
        f"QTabBar::tab:selected {{\n    background: {islands_colors.primary};\n"
        f"    color: white;\n    border: 1px solid {islands_colors.border};"
    ) in css
    assert (
        f"QTabWidget::pane {{\n    border: none;\n"
        f"    border-top: 1px solid {islands_colors.sea};\n"
        f"    background-color: {islands_colors.surface};"
    ) in css
    # QGroupBox: top-only sea divider + surface title.
    assert (
        f"QGroupBox {{\n    border: none;\n"
        f"    border-top: 1px solid {islands_colors.sea};"
    ) in css
    assert (
        f"QGroupBox::title {{\n    background-color: {islands_colors.surface};"
    ) in css
    assert (
        "QTreeView::item,\nQListView::item,\nQTableView::item {\n    border-radius: 0px;"
    ) in css
    # List rows use horizontal-only padding (vertical padding pushes text low).
    assert "QListView::item {\n    padding: 0px 4px;" in css
    # No focus rectangle around the current cell.
    assert "QTreeView,\nQListView,\nQTableView {\n    outline: none;" in css
    # Context menus under the proxy keep a sea background (panel-transparency
    # rule would otherwise blank them).
    assert (
        f"#TheaterProxy QMenu {{\n    background-color: {islands_colors.sea};"
    ) in css
    # Submenus keep a sea background (the 'QMenu QWidget' corner-fix rule
    # would otherwise blank nested menus).
    assert (
        f"QMenu QMenu {{\n    background-color: {islands_colors.sea};"
    ) in css
    assert (
        f"QStackedWidget {{\n    background-color: {islands_colors.surface};"
    ) in css
    assert (
        f"QDialog {{\n    background-color: {islands_colors.surface};"
    ) in css
    assert (
        f"QPushButton {{\n    background: {islands_colors.sea};\n    border: none;"
    ) in css


def test_island_widget_polish_absent_when_flat(flat_colors):
    """Audit: the islands sea/surface aesthetic (polish rules) must not be
    emitted for non-islands themes — only the foundational dock chrome, which
    uses the background/surface fallback variables, applies there."""
    css = generate_docking_stylesheet(flat_colors)
    assert "QTabBar::tab:selected" not in css
    assert "QStackedWidget {" not in css
    assert "QDialog {" not in css
    assert "QGroupBox::title" not in css
    assert "outline: none;" not in css
    assert "QListView::item {" not in css
    # And the distinct sea color never appears for a flat theme (sea falls
    # back to background, so no separate sea value leaks into the sheet).
    assert flat_colors.sea == flat_colors.background


def test_panel_scroll_content_rule_absent_when_flat(flat_colors):
    css = generate_docking_stylesheet(flat_colors)
    assert "QDockWidget QScrollArea > QWidget > QWidget" not in css


def test_central_logbook_is_surface_and_rounded(islands_colors):
    """The central widget (logbook) is a SHELL: surface bg + rounded corners +
    gap margin. Its interior is container (transparent via the global rules),
    so the rounded surface shows through — no per-scroll-area surface rule."""
    css = generate_docking_stylesheet(islands_colors)
    assert (
        "#InnerDockWindow > QWidget {\n"
        f"    background-color: {islands_colors.surface};"
    ) in css
    # The old opaque central scroll-area rule is gone (it fought the global
    # transparency and re-squared the corner).
    assert "#InnerDockWindow > QWidget > QScrollArea {" not in css


def test_central_shell_rule_absent_when_flat(flat_colors):
    """The central surface+rounding shell only applies in islands mode."""
    css = generate_docking_stylesheet(flat_colors)
    assert "#InnerDockWindow > QWidget {" not in css
    assert "#InnerDockWindow > QWidget > QScrollArea {" not in css
