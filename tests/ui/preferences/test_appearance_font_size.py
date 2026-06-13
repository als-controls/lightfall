"""Font-size changes must propagate to the whole UI, not just plots/menus.

The base font size is carried in the generated stylesheet so that changing it
re-polishes every widget. QApplication.setFont() alone does not restyle
widgets that were already shown while a global stylesheet is active — only
pyqtgraph (reads the app font live) and menus (re-polished on show) picked it
up, leaving the rest of the main window stuck at the old size.
"""

from __future__ import annotations

from lightfall.ui.theme import ThemeManager


def test_generate_stylesheet_carries_base_font_size(qtbot):
    """The base font size must appear as a font-size rule in the stylesheet."""
    ThemeManager.reset()
    mgr = ThemeManager.get_instance()

    mgr.set_font_size(17)

    assert "font-size: 17pt" in mgr.generate_stylesheet()


def test_set_font_size_emits_theme_changed_to_trigger_reapply(qtbot):
    """Changing the size must emit theme_changed so the stylesheet reapplies."""
    ThemeManager.reset()
    mgr = ThemeManager.get_instance()
    # Move off the default so the change is real.
    mgr.set_font_size(12)

    seen: list[str] = []
    mgr.theme_changed.connect(seen.append)

    mgr.set_font_size(15)
    assert seen, "set_font_size should emit theme_changed when the size changes"

    # No-op change must not re-emit (avoids redundant full restyle).
    seen.clear()
    mgr.set_font_size(15)
    assert not seen, "set_font_size should be a no-op when the size is unchanged"


def test_scale_helpers_track_base_font_size(qtbot):
    """scale_pt / scale_px are identity at 10pt and scale linearly."""
    ThemeManager.reset()
    mgr = ThemeManager.get_instance()

    mgr.set_font_size(10)
    assert mgr.scale_pt(8) == 8
    assert mgr.scale_px(17) == 17

    mgr.set_font_size(20)
    assert mgr.scale_pt(8) == 16
    assert mgr.scale_px(17) == 34


def test_docking_stylesheet_scales_chrome_fonts(qtbot):
    """Dock chrome (titles/headers) px font sizes scale with the base font."""
    from lightfall.ui.docking.theme import generate_docking_stylesheet
    from lightfall.ui.theme.manager import LIGHT_COLORS

    at10 = generate_docking_stylesheet(LIGHT_COLORS, islands=True, font_size=10)
    at20 = generate_docking_stylesheet(LIGHT_COLORS, islands=True, font_size=20)
    assert "font-size: 11px" in at10 and "font-size: 12px" in at10
    assert "font-size: 22px" in at20 and "font-size: 24px" in at20


def test_logbook_entry_rows_grow_with_base_font(qtbot):
    """The custom-painted entries list relayouts when the base font grows.

    Mirrors the real path: set_font_size() + the stylesheet reapply that the
    main window performs on theme_changed repolishes the view, so the delegate
    paints/sizes against the new cascaded font.
    """
    from PySide6.QtWidgets import QApplication, QListView

    from lightfall.logbook.entry_widget import EntryData, EntryListWidget

    ThemeManager.reset()
    mgr = ThemeManager.get_instance()
    widget = EntryListWidget()
    qtbot.addWidget(widget)
    widget.set_entries([EntryData(title="An entry", tags=["x"])])
    view = widget.findChild(QListView)
    view.resize(300, 200)
    idx = view.model().index(0, 0)
    app = QApplication.instance()

    mgr.set_font_size(10)
    mgr.apply_to_application()
    app.processEvents()
    h10 = view.visualRect(idx).height()

    mgr.set_font_size(22)
    mgr.apply_to_application()
    app.processEvents()
    h22 = view.visualRect(idx).height()

    assert h22 > h10, "entry rows must grow with the base font size"
