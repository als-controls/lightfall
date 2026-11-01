"""Assistant Tools page: the scope-aware catalog writing the scoped ``tools.enabled``.

The old page wrote two lists, ``disabled_tool_plugins`` and ``forced_enabled_tool_plugins``;
see commit bb0b5c72 for its characterization. Here the same toggles become dict entries:
``{name: False}`` for each newly disabled default-on tool and ``{name: True}`` for a
default-off tool switched on.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QComboBox, QTableWidget

from lightfall.plugins.tool_plugin import ToolPlugin
from lightfall.ui.panels.claude.tool_registry import ToolRegistry
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.scope_api import ScopeChoice
from lightfall.ui.preferences.tool_settings import ClaudeToolsSettingsPlugin, SkillSettingsPlugin


def make_tool(name, category="general", default=True):
    return type(f"Tool_{name}", (ToolPlugin,), {
        "name": property(lambda self: name),
        "description": property(lambda self: f"{name} plugin"),
        "category": property(lambda self: category),
        "enabled_by_default": property(lambda self: default),
    })()


KEY = "tools.enabled"
USER, LOCAL, BEAMLINE, FACILITY = "user:u1", "local", "beamline:7.0.1", "facility"


class FakePrefs(QObject):
    scopes_changed = Signal(object)

    def __init__(self, layers=None, inherited=None, writable=(USER, BEAMLINE)):
        super().__init__()
        self.layers = dict(layers or {})
        self.inherited = dict(inherited or {})
        self.writable = tuple(writable)
        self.calls: list[tuple] = []

    def scope_choices(self):
        return [
            ScopeChoice(USER, "user", "User", None, ""),
            ScopeChoice(BEAMLINE, "beamline", "Beamline", "operator", ""),
            ScopeChoice(FACILITY, "facility", "Facility", "viewer", "Read-only"),
        ]

    def default_scope(self):
        return USER

    def allowed_at(self, key, scope):
        return scope != LOCAL

    def writable_at(self, key, scope):
        return scope in self.writable

    def lock_at(self, key, scope):
        return None

    def layer_value(self, key, scope):
        return self.layers.get((scope, key))

    def inherited_items(self, key, scope):
        return self.inherited if scope == USER else {}

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
    ToolRegistry.reset_instance()
    reg = ToolRegistry.get_instance()
    for tool in (make_tool("alpha"), make_tool("beta", "devices"), make_tool("gamma", "devices", False)):
        reg.register(tool)
    pages = [ClaudeToolsSettingsPlugin()]
    prefs = FakePrefs()
    registry = SimpleNamespace(
        get_ready_by_type=lambda _t: [SimpleNamespace(instance=p) for p in pages],
        get_all=lambda: [],
    )
    services = SimpleNamespace(get=lambda _cls: registry)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance", classmethod(lambda cls: services)
    )
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: prefs)
    )
    yield SimpleNamespace(prefs=prefs, pages=pages)
    ToolRegistry.reset_instance()


def _dialog(qtbot):
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)

    def no_restart():
        raise AssertionError("the Tools page must not ask for a restart")

    dialog._prompt_restart = no_restart
    return dialog


def _row(dialog) -> SettingRow:
    form = next(f for f in dialog.findChildren(SettingsForm) if f._plugin.name == "claude_tools")
    return next(r for r in form.findChildren(SettingRow) if r.key == KEY)


def _combo(dialog, name) -> QComboBox:
    return _row(dialog).editor.item_combo(name)


def _pick(dialog, name, text):
    box = _combo(dialog, name)
    box.setCurrentIndex(box.findText(text))


def test_skill_settings_alias_still_works():
    assert SkillSettingsPlugin is ClaudeToolsSettingsPlugin


def test_page_declares_a_union_spec_for_every_scope_but_this_console():
    (spec,) = ClaudeToolsSettingsPlugin().specs()
    assert (spec.key, spec.policy, spec.type) == (KEY, "union", bool)
    assert spec.scopes == {"user", "branch", "beamline", "facility"}


def test_page_lists_registered_tools_by_category(qtbot, env):
    dialog = _dialog(qtbot)
    table = _row(dialog).editor.findChild(QTableWidget)
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers[:3] == ["Name", "Category", "Description"]
    rows = [[table.item(r, c).text() for c in range(3)] for r in range(table.rowCount())]
    assert rows == [  # sorted by category, then display name, as the old table was
        ["Beta", "Devices", "beta plugin"],
        ["Gamma", "Devices", "gamma plugin"],
        ["Alpha", "General", "alpha plugin"],
    ]


def test_toggles_at_user_write_the_enabled_map(qtbot, env):
    dialog = _dialog(qtbot)
    _pick(dialog, "alpha", "Off")
    _pick(dialog, "gamma", "On")  # ships off; On is an explicit override
    assert dialog._apply() is True
    assert env.prefs.calls == [(KEY, {"alpha": False, "gamma": True}, USER)]


def test_inherited_default_shown_without_a_broader_value(qtbot, env):
    dialog = _dialog(qtbot)
    table = _row(dialog).editor.findChild(QTableWidget)
    last = table.columnCount() - 1
    inherited = {table.item(r, 0).text(): table.item(r, last).text() for r in range(table.rowCount())}
    assert inherited["Gamma"] == "Off (default)"
    assert inherited["Alpha"] == "On (default)"


def test_beamline_value_is_inherited_and_shown_at_user(qtbot, env):
    env.prefs.inherited = {"beta": (False, "Beamline 7.0.1")}
    dialog = _dialog(qtbot)
    table = _row(dialog).editor.findChild(QTableWidget)
    last = table.columnCount() - 1
    inherited = {table.item(r, 0).text(): table.item(r, last).text() for r in range(table.rowCount())}
    assert inherited["Beta"] == "Off (Beamline 7.0.1)"
    assert _combo(dialog, "beta").currentText() == "Not set"  # the user's own layer stays empty
    assert dialog._apply() is True
    assert env.prefs.calls == []


def test_tools_at_beamline(qtbot, env):
    dialog = _dialog(qtbot)
    dialog.select_scope(BEAMLINE)
    _pick(dialog, "alpha", "Off")
    assert dialog._apply() is True
    assert env.prefs.calls == [(KEY, {"alpha": False}, BEAMLINE)]


def test_tools_read_only_at_facility_for_a_viewer(qtbot, env):
    dialog = _dialog(qtbot)
    dialog.select_scope(FACILITY)
    assert not _combo(dialog, "alpha").isEnabled()


def test_user_layer_values_show_in_the_combos(qtbot, env):
    env.prefs.layers[(USER, KEY)] = {"alpha": False, "gamma": True}
    dialog = _dialog(qtbot)
    assert _combo(dialog, "alpha").currentText() == "Off"
    assert _combo(dialog, "gamma").currentText() == "On"
    assert _combo(dialog, "beta").currentText() == "Not set"


def test_no_restart_field_flag():
    (section,) = ClaudeToolsSettingsPlugin().sections()
    (field,) = section.items
    assert not field.restart_required
