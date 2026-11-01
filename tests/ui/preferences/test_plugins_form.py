"""Plugins page: the scope-aware catalog writing the scoped ``plugins.enabled``.

The old page wrote ``disabled_plugins`` (a list); see commit 4410801d for its characterization.
Here the same toggles become dict entries: ``{id: False}`` for each newly disabled plugin and
``{id: True}`` for a default-disabled plugin switched on.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QComboBox, QTableWidget

from lightfall.plugins.errors import PluginStatus
from lightfall.plugins.info import PluginInfo
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.plugin_settings import PluginSettingsPlugin
from lightfall.ui.preferences.scope_api import ScopeChoice


def _info(type_name, name, status=PluginStatus.READY, manifest="core"):
    return PluginInfo(
        type_name=type_name, name=name, import_path=f"x.{name}:C", manifest_name=manifest, status=status
    )


PLUGINS = [
    _info("tool", "alpha"),
    _info("tool", "beta", PluginStatus.FAILED_LOAD),
    _info("settings", "plugins"),
    _info("auth_provider", "local", PluginStatus.DISABLED),
]


KEY = "plugins.enabled"
USER, LOCAL, BEAMLINE, FACILITY = "user:u1", "local", "beamline:7.0.1", "facility"


class FakePrefs(QObject):
    scopes_changed = Signal(object)

    def __init__(self, layers=None, writable=(USER, LOCAL, BEAMLINE)):
        super().__init__()
        self.layers = dict(layers or {})
        self.writable = tuple(writable)
        self.calls: list[tuple] = []

    def scope_choices(self):
        return [
            ScopeChoice(USER, "user", "User", None, ""),
            ScopeChoice(LOCAL, "local", "This console", None, ""),
            ScopeChoice(BEAMLINE, "beamline", "Beamline", "operator", ""),
            ScopeChoice(FACILITY, "facility", "Facility", "viewer", "Read-only"),
        ]

    def default_scope(self):
        return USER

    def allowed_at(self, key, scope):
        return True

    def writable_at(self, key, scope):
        return scope in self.writable

    def lock_at(self, key, scope):
        return None

    def layer_value(self, key, scope):
        return self.layers.get((scope, key))

    def inherited_items(self, key, scope):
        return {}

    def get(self, key, default=None):
        return default

    def set(self, key, value, *, scope=None):
        self.calls.append((key, value, scope))

    def remove(self, key, *, scope=None):
        self.calls.append((key, None, scope))

    def subscribe(self, key, slot):
        pass

    def unsubscribe(self, key, slot):
        pass


@pytest.fixture
def env(monkeypatch):
    plugins = [PluginSettingsPlugin()]
    prefs = FakePrefs()
    registry = SimpleNamespace(
        get_ready_by_type=lambda _t: [SimpleNamespace(instance=p) for p in plugins],
        get_all=lambda: list(PLUGINS),
    )
    services = SimpleNamespace(get=lambda _cls: registry)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance", classmethod(lambda cls: services)
    )
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: prefs)
    )
    return SimpleNamespace(prefs=prefs, plugins=plugins)


def _dialog(qtbot):
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    dialog._prompt_restart = lambda: None
    return dialog


def _row(dialog) -> SettingRow:
    form = next(f for f in dialog.findChildren(SettingsForm) if f._plugin.name == "plugins")
    return next(r for r in form.findChildren(SettingRow) if r.key == KEY)


def _combo(dialog, plugin_id) -> QComboBox:
    return _row(dialog).editor.item_combo(plugin_id)


def _pick(dialog, plugin_id, text):
    box = _combo(dialog, plugin_id)
    box.setCurrentIndex(box.findText(text))


def test_page_lists_registry_plugins_with_live_status(qtbot, env):
    dialog = _dialog(qtbot)
    table = _row(dialog).editor.findChild(QTableWidget)
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers[:4] == ["Name", "Type", "Status", "Manifest"]
    rows = [[table.item(r, c).text() for c in range(4)] for r in range(table.rowCount())]
    assert rows == [  # sorted by type, then name, as the old table was
        ["local", "auth_provider", "DISABLED", "core"],
        ["plugins", "settings", "READY", "core"],
        ["alpha", "tool", "READY", "core"],
        ["beta", "tool", "FAILED_LOAD", "core"],
    ]


def test_toggles_at_user_write_the_enabled_map(qtbot, env):
    dialog = _dialog(qtbot)
    _pick(dialog, "tool:alpha", "Off")
    _pick(dialog, "auth_provider:local", "On")  # ships disabled; On is an explicit override
    assert dialog._apply() is True
    assert env.prefs.calls == [
        (KEY, {"tool:alpha": False, "auth_provider:local": True}, USER)
    ]


def test_inherited_default_shown_for_default_disabled_plugin(qtbot, env):
    dialog = _dialog(qtbot)
    table = _row(dialog).editor.findChild(QTableWidget)
    last = table.columnCount() - 1
    inherited = {table.item(r, 0).text(): table.item(r, last).text() for r in range(table.rowCount())}
    assert inherited["local"] == "Off (default)"
    assert inherited["alpha"] == "On (default)"
    assert inherited["plugins"] == "On (always)"


def test_plugins_at_this_console(qtbot, env):
    dialog = _dialog(qtbot)
    dialog.select_scope(LOCAL)
    _pick(dialog, "tool:beta", "Off")
    assert dialog._apply() is True
    assert env.prefs.calls == [(KEY, {"tool:beta": False}, LOCAL)]


def test_plugins_at_beamline(qtbot, env):
    dialog = _dialog(qtbot)
    dialog.select_scope(BEAMLINE)
    _pick(dialog, "tool:alpha", "Off")
    assert dialog._apply() is True
    assert env.prefs.calls == [(KEY, {"tool:alpha": False}, BEAMLINE)]


def test_plugins_read_only_at_facility_for_a_viewer(qtbot, env):
    dialog = _dialog(qtbot)
    dialog.select_scope(FACILITY)
    assert not _combo(dialog, "tool:alpha").isEnabled()


def test_protected_plugin_stays_on(qtbot, env):
    dialog = _dialog(qtbot)
    box = _combo(dialog, "settings:plugins")
    assert not box.isEnabled() and box.currentText() == "On"
    env.prefs.layers[(USER, KEY)] = {"settings:plugins": False}
    dialog._refresh_pages()
    box = _combo(dialog, "settings:plugins")
    assert box.currentText() == "On"
    _pick(dialog, "tool:alpha", "Off")
    assert dialog._apply() is True
    assert env.prefs.calls == [(KEY, {"tool:alpha": False}, USER)]


def test_restart_prompt_after_change(qtbot, env):
    dialog = _dialog(qtbot)
    prompts = []
    dialog._prompt_restart = lambda: prompts.append(1)
    _pick(dialog, "tool:alpha", "Off")
    assert dialog._apply() is True
    assert prompts == [1]


def test_no_restart_prompt_without_change(qtbot, env):
    dialog = _dialog(qtbot)
    prompts = []
    dialog._prompt_restart = lambda: prompts.append(1)
    assert dialog._apply() is True
    assert prompts == []
    assert env.prefs.calls == []


def test_here_combos_keep_theme_colours_on_first_load(qtbot, env):
    """Set rows show normal text, only Not set is muted, straight after the dialog is built.

    The app stylesheet rewrites each combo's palette when it first polishes; the editor
    must re-apply its muting after that (a scope switch used to be the only fix).
    """
    from PySide6.QtGui import QColor, QPalette

    from lightfall.ui.theme import ThemeManager

    theme = ThemeManager.get_instance()
    theme.set_theme_by_name("dark")
    theme.apply_to_application()
    env.prefs.layers[(USER, KEY)] = {"tool:alpha": False, "auth_provider:local": True}
    dialog = _dialog(qtbot)
    dialog.resize(900, 600)
    dialog.show()
    dialog.select_page("plugins")
    qtbot.wait(50)

    def text_color(plugin_id):
        pal = _combo(dialog, plugin_id).palette()
        return pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)

    normal, muted = QColor(theme.colors.text), QColor(theme.colors.text_secondary)
    assert normal != muted
    assert text_color("tool:alpha") == normal  # Off
    assert text_color("auth_provider:local") == normal  # On
    assert text_color("tool:beta") == muted  # Not set
