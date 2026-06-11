"""AppearanceSettingsPlugin must not drive the ThemeManager on load.

Opening the Preferences dialog populates the appearance controls from saved
preferences. Those programmatic writes must NOT be mistaken for user edits:
firing apply_preview() during load re-applies the already-active theme (a full
stylesheet regen + pyqtgraph re-theme), which stutters the dialog open.
"""

from __future__ import annotations

import pytest

from lightfall.ui.preferences.builtin import AppearanceSettingsPlugin
from lightfall.ui.theme import ThemeManager


class _FakePrefs:
    """Minimal PreferencesManager stand-in for the appearance plugin."""

    def __init__(self, theme: str, islands: bool, font_size: int = 10) -> None:
        self.theme = theme
        self.font_size = font_size
        self._islands = islands

    def get(self, key: str, default=None):
        if key == "islands_mode":
            return self._islands
        if key == "console_syntax_style":
            return ""
        return default


@pytest.fixture
def fake_prefs(monkeypatch):
    prefs = _FakePrefs(theme="slate", islands=True)
    monkeypatch.setattr(
        "lightfall.ui.preferences.builtin.PreferencesManager.get_instance",
        lambda: prefs,
    )
    return prefs


def test_load_settings_does_not_drive_theme_manager(qtbot, fake_prefs, monkeypatch):
    """load_settings() is pure initialization and must not touch the theme."""
    ThemeManager.reset()
    plugin = AppearanceSettingsPlugin()
    widget = plugin.create_widget()  # connects currentIndexChanged / toggled
    qtbot.addWidget(widget)

    theme_mgr = ThemeManager.get_instance()
    calls: list[tuple] = []
    monkeypatch.setattr(
        theme_mgr, "set_theme_by_name",
        lambda *a, **k: calls.append(("set_theme_by_name", a)),
    )
    monkeypatch.setattr(
        theme_mgr, "set_islands_mode",
        lambda *a, **k: calls.append(("set_islands_mode", a)),
    )

    plugin.load_settings()

    assert calls == [], (
        "load_settings() fired apply_preview() and re-applied the theme; "
        f"unexpected ThemeManager calls: {calls}"
    )
