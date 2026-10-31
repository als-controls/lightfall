"""Appearance page on the scope-aware form: writes, live preview, beamline defaults.

EXPECTED_APPEARANCE was characterized against the old hand-built page (commit
69b4b63f) for the same edits.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QComboBox, QLineEdit

from lightfall.ui.preferences.builtin import AppearanceSettingsPlugin
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.scope_api import ScopeChoice
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


# The migrated page ------------------------------------------------------------

USER, LOCAL, BRANCH, BEAMLINE, FACILITY = "user:u1", "local", "branch:b", "beamline:7.0.1", "facility"
KINDS = {USER: "user", BRANCH: "branch", BEAMLINE: "beamline", FACILITY: "facility"}
ORDER = [USER, BRANCH, BEAMLINE, FACILITY]  # most specific first
APPEARANCE_KEYS = ("theme", "font_size", "console_syntax_style", "islands_mode")


class FakePrefs(QObject):
    """Scoped preferences stand-in: per-scope layers, resolved user > ... > facility."""

    scopes_changed = Signal(object)

    def __init__(self, plugins):
        super().__init__()
        self.allowed = {}
        for plugin in plugins:
            for spec in plugin.specs():
                self.allowed[spec.key] = {s for s, k in KINDS.items() if k in spec.scopes}
        self.layers: dict = {}
        self.calls: list[tuple] = []

    def scope_choices(self):
        return [
            ScopeChoice(USER, "user", "User", None, ""),
            ScopeChoice(LOCAL, "local", "This console", None, ""),
            ScopeChoice(BRANCH, "branch", "Branch", "operator", ""),
            ScopeChoice(BEAMLINE, "beamline", "Beamline", "operator", ""),
            ScopeChoice(FACILITY, "facility", "Facility", "maintainer", ""),
        ]

    def default_scope(self):
        return USER

    def allowed_at(self, key, scope):
        return scope in self.allowed.get(key, set())

    def writable_at(self, key, scope):
        return self.allowed_at(key, scope)

    def needs_approval(self, key, scope):
        return False

    def lock_at(self, key, scope):
        return None

    def layer_value(self, key, scope):
        return self.layers.get((scope, key))

    def get(self, key, default=None):
        for scope in ORDER:
            value = self.layers.get((scope, key))
            if value is not None:
                return value
        return default

    @property
    def theme(self):
        return self.get("theme", "system")

    @property
    def font_size(self):
        return self.get("font_size", 10)

    def set(self, key, value, *, scope=None):
        self.calls.append((key, value, scope))
        self.layers[(scope, key)] = value

    def remove(self, key, *, scope=None):
        self.calls.append((key, None, scope))
        self.layers.pop((scope, key), None)

    def subscribe(self, key, slot):
        pass

    def unsubscribe(self, key, slot):
        pass


@pytest.fixture
def env(monkeypatch, themes, app_font):
    plugins = [AppearanceSettingsPlugin()]
    prefs = FakePrefs(plugins)
    registry = SimpleNamespace(get_ready_by_type=lambda _t: [SimpleNamespace(instance=p) for p in plugins])
    services = SimpleNamespace(get=lambda _cls: registry)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance", classmethod(lambda cls: services)
    )
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: prefs)
    )
    tm = ThemeManager.get_instance()
    tm.set_theme_by_name("light")
    tm.set_islands_mode(False)
    AppearanceSettingsPlugin._apply_font_size(10)
    return SimpleNamespace(prefs=prefs, plugin=plugins[0], tm=tm)


def _dialog(qtbot):
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    return dialog


def _row(dialog, key) -> SettingRow:
    form = next(f for f in dialog.findChildren(SettingsForm) if f._plugin.name == "appearance")
    return next(r for r in form.findChildren(SettingRow) if r.key == key)


def _choose(row, text):
    combo = row.editor.findChild(QComboBox)
    index = combo.findText(text)
    assert index >= 0, f"{text!r} not offered"
    combo.setCurrentIndex(index)  # emits like a user pick


def _type(qtbot, row, text):
    line = row.editor.findChild(QLineEdit)
    line.clear()
    qtbot.keyClicks(line, text)


def _app_point_size() -> int:
    from PySide6.QtWidgets import QApplication

    return QApplication.instance().font().pointSize()


def _edit_all(qtbot, dialog):
    _choose(_row(dialog, "theme"), "Slate (Dark)")
    _type(qtbot, _row(dialog, "font_size"), "14")
    _choose(_row(dialog, "console_syntax_style"), "Monokai")
    _choose(_row(dialog, "islands_mode"), "On")


def test_appearance_specs_allow_beamline_defaults():
    specs = {s.key: s.scopes for s in AppearanceSettingsPlugin().specs()}
    assert set(specs) == set(APPEARANCE_KEYS)
    assert all(scopes == {"user", "branch", "beamline", "facility"} for scopes in specs.values())


def test_appearance_user_scope_writes_same_keys(qtbot, env):
    dialog = _dialog(qtbot)
    _edit_all(qtbot, dialog)
    assert dialog._apply() is True
    assert {k: v for k, v, _s in env.prefs.calls} == EXPECTED_APPEARANCE
    assert {s for _k, _v, s in env.prefs.calls} == {USER}


def test_untouched_appearance_page_writes_nothing(qtbot, env):
    dialog = _dialog(qtbot)
    assert dialog._apply() is True
    assert env.prefs.calls == []


def test_theme_options_come_from_the_theme_manager(qtbot, env):
    dialog = _dialog(qtbot)
    combo = _row(dialog, "theme").editor.findChild(QComboBox)
    labels = [combo.itemText(i) for i in range(combo.count())]
    assert {"System", "Light", "Slate (Dark)"} <= set(labels)


def test_preview_applies_theme_and_cancel_reverts(qtbot, env):
    dialog = _dialog(qtbot)
    _choose(_row(dialog, "theme"), "Slate (Dark)")
    assert env.tm.theme_name == "slate"
    _type(qtbot, _row(dialog, "font_size"), "18")
    assert env.tm.base_font_size == 18
    assert _app_point_size() == 18
    _choose(_row(dialog, "islands_mode"), "On")
    assert env.tm.islands_mode is True

    dialog.reject()

    assert env.tm.theme_name == "light"
    assert env.tm.base_font_size == 10
    assert _app_point_size() == 10
    assert env.tm.islands_mode is False
    assert env.prefs.calls == []


def test_cancel_without_preview_leaves_theme_alone(qtbot, env, monkeypatch):
    dialog = _dialog(qtbot)
    calls = []
    monkeypatch.setattr(env.tm, "set_theme_by_name", lambda *a: calls.append(a))
    dialog.reject()
    assert calls == []


def test_preview_ignored_outside_user_scope(qtbot, env):
    dialog = _dialog(qtbot)
    dialog.select_scope(BEAMLINE)
    _choose(_row(dialog, "theme"), "Slate (Dark)")
    _type(qtbot, _row(dialog, "font_size"), "18")
    _choose(_row(dialog, "islands_mode"), "On")
    assert env.tm.theme_name == "light"
    assert env.tm.base_font_size == 10
    assert env.tm.islands_mode is False


def test_beamline_default_applies_only_without_a_user_value(qtbot, env):
    env.prefs.layers[(USER, "theme")] = "light"
    dialog = _dialog(qtbot)
    dialog.select_scope(BEAMLINE)
    _choose(_row(dialog, "theme"), "Slate (Dark)")
    _type(qtbot, _row(dialog, "font_size"), "16")
    assert dialog._apply() is True
    assert ("theme", "slate", BEAMLINE) in env.prefs.calls
    assert env.tm.theme_name == "light"  # the user's own theme wins
    assert env.tm.base_font_size == 16  # no user font size: the beamline's applies


def test_apply_then_cancel_keeps_what_was_saved(qtbot, env):
    dialog = _dialog(qtbot)
    _choose(_row(dialog, "theme"), "Slate (Dark)")
    assert dialog._apply() is True
    dialog.reject()
    assert env.tm.theme_name == "slate"


def test_syntax_style_auto_is_a_value(qtbot, env):
    dialog = _dialog(qtbot)
    row = _row(dialog, "console_syntax_style")
    assert row.editor.value() is None  # not set yet
    _choose(row, "Auto (follows theme)")
    assert row.editor.value() == ""
    assert dialog._apply() is True
    assert env.prefs.calls == [("console_syntax_style", "", USER)]


def test_stored_auto_syntax_style_shows_as_auto(qtbot, env):
    env.prefs.layers[(USER, "console_syntax_style")] = ""
    dialog = _dialog(qtbot)
    row = _row(dialog, "console_syntax_style")
    assert row.editor.value() == ""
    assert row.editor.findChild(QComboBox).currentText() == "Auto (follows theme)"


def test_undone_preview_leaves_nothing_to_revert(qtbot, env):
    env.prefs.layers[(USER, "theme")] = "light"
    dialog = _dialog(qtbot)
    _choose(_row(dialog, "theme"), "Slate (Dark)")
    _choose(_row(dialog, "theme"), "Light")  # back to the saved theme: not an edit
    assert env.plugin._original is None
    dialog.accept()
    assert env.prefs.calls == []
