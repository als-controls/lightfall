"""Appearance page: the keys it writes (characterized against the old page)."""

from __future__ import annotations

import pytest

from lightfall.ui.preferences.builtin import AppearanceSettingsPlugin
from lightfall.ui.theme import ThemeManager, ThemeRegistry

EXPECTED_APPEARANCE = {
    "theme": "slate",
    "font_size": 14,
    "console_syntax_style": "monokai",
    "islands_mode": True,
}


@pytest.fixture
def themes():
    """Light and Slate registered, as the plugin loader does at startup."""
    from lightfall.ui.theme.builtin import LightThemePlugin, SlateThemePlugin

    ThemeRegistry.reset()
    registry = ThemeRegistry.get_instance()
    registry.register(LightThemePlugin())
    registry.register(SlateThemePlugin())
    ThemeManager.reset()
    yield registry
    ThemeRegistry.reset()
    ThemeManager.reset()


@pytest.fixture
def app_font():
    """Restore the application font size after a test previews another."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    size = app.font().pointSize()
    yield
    font = app.font()
    font.setPointSize(size)
    app.setFont(font)


class RecordingPrefs:
    def __init__(self):
        self.calls: list[tuple] = []

    def get(self, key, default=None):
        return default

    def set(self, key, value, **kwargs):
        self.calls.append((key, value))

    @property
    def theme(self):
        return self.get("theme", "system")

    @theme.setter
    def theme(self, value):
        self.set("theme", value)

    @property
    def font_size(self):
        return self.get("font_size", 10)

    @font_size.setter
    def font_size(self, value):
        self.set("font_size", value)


@pytest.fixture
def prefs(monkeypatch):
    from lightfall.ui.preferences.manager import PreferencesManager

    rec = RecordingPrefs()
    monkeypatch.setattr(PreferencesManager, "get_instance", classmethod(lambda cls: rec))
    return rec


def test_appearance_writes_same_keys(qapp, prefs, themes, app_font):
    page = AppearanceSettingsPlugin()
    page.create_widget()
    page.load_settings()
    page._theme_combo.setCurrentIndex(page._theme_combo.findData("slate"))
    page._font_spin.setValue(14)
    page._console_style_combo.setCurrentIndex(page._console_style_combo.findData("monokai"))
    page._islands_check.setChecked(True)
    page.save_settings()
    assert dict(prefs.calls) == EXPECTED_APPEARANCE
