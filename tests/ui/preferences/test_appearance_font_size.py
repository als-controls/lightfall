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
