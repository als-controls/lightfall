"""Tests for the docking stylesheet generator (islands vs non-islands).

``generate_docking_stylesheet`` is a pure function (ThemeColors -> CSS string),
so these tests assert on the generated CSS without needing a QApplication.
"""

from __future__ import annotations

import pytest

from lightfall.ui.docking.theme import _is_islands_mode, generate_docking_stylesheet
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
    """PanelPlugin (dock content) background uses Surface."""
    css = generate_docking_stylesheet(islands_colors)
    assert (
        "QDockWidget > QWidget {\n"
        f"    background: {islands_colors.surface};"
    ) in css


def test_panel_scroll_subtree_transparent_in_islands(islands_colors):
    """A BasePanel's QScrollArea (viewport + scrolled child) autofills the
    Window role (= sea) and, as opaque squares, paints sea and squares off
    the panel's rounded corners. Islands mode makes the whole scroll subtree
    transparent so the rounded surface painted by the panel's ancestor shows
    through — the central BasePanel for the logbook and the dock content
    wrapper (TheaterProxy, matched by QDockWidget > QWidget) for dock panels."""
    css = generate_docking_stylesheet(islands_colors)
    assert (
        "QDockWidget QScrollArea,\n"
        "QDockWidget QScrollArea > QWidget,\n"
        "QDockWidget QScrollArea > QWidget > QWidget,\n"
        "#InnerDockWindow > QWidget QScrollArea,\n"
        "#InnerDockWindow > QWidget QScrollArea > QWidget,\n"
        "#InnerDockWindow > QWidget QScrollArea > QWidget > QWidget {\n"
        "    background-color: rgba(0, 0, 0, 0);"
    ) in css


def test_dock_panel_subtree_transparent_in_islands(islands_colors):
    """The dock panel subtree sits on top of its TheaterProxy and, left
    opaque, squares off the proxy's rounded corners. Islands mode makes it
    transparent so the rounded surface shows. Must be a descendant combinator
    (#TheaterProxy QWidget) — Qt's '>' does not match QStackedWidget pages."""
    css = generate_docking_stylesheet(islands_colors)
    assert (
        "#TheaterProxy QWidget {\n"
        "    background-color: rgba(0, 0, 0, 0);"
    ) in css
    # The direct-child form silently misses the stacked panel; guard against
    # a well-meaning "simplification" back to it.
    assert "#TheaterProxy > QWidget {" not in css


def test_dock_panel_subtree_rule_absent_when_flat(flat_colors):
    css = generate_docking_stylesheet(flat_colors)
    assert "#TheaterProxy QWidget" not in css


def test_island_widget_polish(islands_colors):
    """Islands themes: selected tab = surface, stacked widget = surface,
    push buttons flat/sea/borderless."""
    css = generate_docking_stylesheet(islands_colors)
    # Idle tabs: surface, borderless, rounded-rect (button-like).
    assert (
        f"QTabBar::tab {{\n    background: {islands_colors.surface};\n"
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
        f"    background: {islands_colors.surface};"
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
        f"QStackedWidget {{\n    background: {islands_colors.surface};"
    ) in css
    assert (
        f"QPushButton {{\n    background: {islands_colors.sea};\n    border: none;"
    ) in css


def test_island_widget_polish_absent_when_flat(flat_colors):
    css = generate_docking_stylesheet(flat_colors)
    assert "QTabBar::tab:selected" not in css
    assert "QStackedWidget {" not in css


def test_panel_scroll_content_rule_absent_when_flat(flat_colors):
    css = generate_docking_stylesheet(flat_colors)
    assert "QDockWidget QScrollArea > QWidget > QWidget" not in css


def test_central_logbook_is_surface_and_rounded(islands_colors):
    """The central widget (logbook) gets Surface bg + rounded corners, and its
    inner scroll area is rounded too so square corners don't paint over it."""
    css = generate_docking_stylesheet(islands_colors)
    # Central island itself
    assert "#InnerDockWindow > QWidget {" in css
    assert f"background: {islands_colors.surface};" in css
    # Inner scroll area rounding — targeted at the central widget only
    # (double child-combinator never matches dock-hosted scroll areas).
    assert "#InnerDockWindow > QWidget > QScrollArea {" in css


def test_central_scroll_area_rule_absent_when_flat(flat_colors):
    """Rounding only applies in islands mode; flat themes are untouched."""
    css = generate_docking_stylesheet(flat_colors)
    assert "#InnerDockWindow > QWidget > QScrollArea {" not in css
