"""AppearanceSettingsPlugin must not drive the ThemeManager on load.

Opening the Preferences dialog populates the appearance controls from saved
preferences. Those programmatic writes must NOT be mistaken for user edits:
previewing during load re-applies the already-active theme (a full stylesheet
regen + pyqtgraph re-theme), which stutters the dialog open.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal

from lightfall.ui.preferences.builtin import AppearanceSettingsPlugin
from lightfall.ui.theme import ThemeManager


class _FakePrefs(QObject):
    """Minimal PreferencesManager stand-in for the appearance plugin."""

    scopes_changed = Signal(object)

    def __init__(self, theme: str, islands: bool, font_size: int = 10) -> None:
        super().__init__()
        self.theme = theme
        self.font_size = font_size
        self._islands = islands

    def default_scope(self) -> str:
        return "user:u1"

    def get(self, key: str, default=None):
        if key == "islands_mode":
            return self._islands
        if key == "console_syntax_style":
            return ""
        if key == "theme":
            return self.theme
        if key == "font_size":
            return self.font_size
        return default


class _Values:
    """FormValues at the user scope with the given pending edits."""

    scope = "user:u1"

    def __init__(self, prefs: _FakePrefs, **edits) -> None:
        self._prefs = prefs
        self._edits = edits

    def get(self, key: str):
        return self._edits.get(key)

    def effective(self, key: str, default=None):
        value = self._edits.get(key)
        return value if value is not None else self._prefs.get(key, default)

    def is_dirty(self, key: str) -> bool:
        return key in self._edits


@pytest.fixture
def fake_prefs(monkeypatch):
    prefs = _FakePrefs(theme="slate", islands=True)
    monkeypatch.setattr(
        "lightfall.ui.preferences.builtin.PreferencesManager.get_instance",
        lambda: prefs,
    )
    return prefs


def test_building_the_form_does_not_drive_theme_manager(qtbot, fake_prefs, monkeypatch):
    """Showing the page with saved values is pure initialization: no theme calls."""
    from lightfall.ui.preferences.form.model import FormModel
    from lightfall.ui.preferences.form.view import SettingsForm

    ThemeManager.reset()
    plugin = AppearanceSettingsPlugin()
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

    class _Store:
        def allowed_at(self, key, scope):
            return True

        writable_at = allowed_at

        def lock_at(self, key, scope):
            return None

        def needs_approval(self, key, scope):
            return False

        def scope_choices(self):
            from lightfall.ui.preferences.scope_api import ScopeChoice

            return [ScopeChoice("user:u1", "user", "User", None, "")]

    saved = {"theme": "slate", "islands_mode": True, "font_size": 12}
    model = FormModel(read_layer=lambda key, scope: saved.get(key))
    form = SettingsForm(plugin, model, _Store())
    qtbot.addWidget(form)
    form.show_scope("user:u1")

    assert calls == [], (
        "showing the form re-applied the theme; "
        f"unexpected ThemeManager calls: {calls}"
    )

def _app_point_size() -> int:
    from PySide6.QtWidgets import QApplication

    return QApplication.instance().font().pointSize()


def _set_app_point_size(size: int) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    font = app.font()
    font.setPointSize(size)
    app.setFont(font)


def test_preview_applies_font_size(qtbot, fake_prefs):
    """Editing the font size applies it app-wide (live preview)."""
    ThemeManager.reset()
    original = _app_point_size()
    try:
        plugin = AppearanceSettingsPlugin()
        plugin.preview(_Values(fake_prefs, font_size=18))  # user edit

        assert _app_point_size() == 18
    finally:
        _set_app_point_size(original)


def test_on_loaded_applies_saved_font_size(qtbot, monkeypatch):
    """Startup preload applies the saved font size, not just the theme."""
    prefs = _FakePrefs(theme="system", islands=False, font_size=20)
    monkeypatch.setattr(
        "lightfall.ui.preferences.builtin.PreferencesManager.get_instance",
        lambda: prefs,
    )
    ThemeManager.reset()
    original = _app_point_size()
    try:
        plugin = AppearanceSettingsPlugin()
        plugin.on_loaded()
        assert _app_point_size() == 20
    finally:
        _set_app_point_size(original)


def test_revert_preview_restores_font_size(qtbot, fake_prefs):
    """Cancelling the dialog restores the font size active when it opened."""
    ThemeManager.reset()
    original = _app_point_size()
    try:
        plugin = AppearanceSettingsPlugin()
        plugin._apply_font_size(10)  # in effect when the dialog opened
        plugin.preview(_Values(fake_prefs, font_size=22))  # preview a bigger size
        plugin.revert_preview()

        assert _app_point_size() == 10
    finally:
        _set_app_point_size(original)


def test_scope_activation_reapplies_islands(qtbot, monkeypatch):
    """islands_mode saved at user scope only resolves after login activates
    scopes (post-preload); the scope change must re-apply it."""
    prefs = _FakePrefs(theme="system", islands=False)
    monkeypatch.setattr(
        "lightfall.ui.preferences.builtin.PreferencesManager.get_instance",
        lambda: prefs,
    )
    ThemeManager.reset()
    plugin = AppearanceSettingsPlugin()
    plugin.on_loaded()
    assert ThemeManager.get_instance().islands_mode is False

    prefs._islands = True  # scope chain now resolves the user's saved value
    prefs.scopes_changed.emit(object())
    assert ThemeManager.get_instance().islands_mode is True
