"""Visualization and Login & Session pages: the keys they write at the user scope."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QAbstractButton, QLineEdit

from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.login_settings import LoginSettingsPlugin
from lightfall.ui.preferences.scope_api import ScopeChoice
from lightfall.ui.preferences.visualization_settings import VisualizationSettingsPlugin

USER, LOCAL, BRANCH, BEAMLINE, FACILITY = "user:u1", "local", "branch:b", "beamline:7.0.1", "facility"
KINDS = {USER: "user", BRANCH: "branch", BEAMLINE: "beamline", FACILITY: "facility"}


class FakePrefs(QObject):
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
            ScopeChoice(BEAMLINE, "beamline", "Beamline", "operator", ""),
            ScopeChoice(FACILITY, "facility", "Facility", "maintainer", ""),
        ]

    def default_scope(self):
        return USER

    def allowed_at(self, key, scope):
        return scope in self.allowed.get(key, set())

    def writable_at(self, key, scope):
        return self.allowed_at(key, scope)

    def lock_at(self, key, scope):
        return None

    def layer_value(self, key, scope):
        return self.layers.get((scope, key))

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
    plugins = [VisualizationSettingsPlugin(), LoginSettingsPlugin()]
    prefs = FakePrefs(plugins)
    registry = SimpleNamespace(get_ready_by_type=lambda _t: [SimpleNamespace(instance=p) for p in plugins])
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
    return dialog


def _form(dialog, name) -> SettingsForm:
    return next(f for f in dialog.findChildren(SettingsForm) if f._plugin.name == name)


def _row(dialog, name, key) -> SettingRow:
    return next(r for r in _form(dialog, name).findChildren(SettingRow) if r.key == key)


def _toggle(qtbot, row, text):
    buttons = {b.text(): b for b in row.editor.findChildren(QAbstractButton)}
    qtbot.mouseClick(buttons[text], Qt.LeftButton)


def _type(qtbot, row, text):
    line = row.editor.findChild(QLineEdit)
    line.clear()
    qtbot.keyClicks(line, text)


def _choose(row, text):
    combo = row.editor._combo
    combo.setCurrentIndex(combo.findText(text))


def test_visualization_user_scope_writes_same_keys(qtbot, env):
    dialog = _dialog(qtbot)
    name = "visualization"
    _toggle(qtbot, _row(dialog, name, "visualization.auto_select"), "Off")
    _choose(_row(dialog, name, "visualization.default_type"), "heatmap")
    _type(qtbot, _row(dialog, name, "visualization.decimation_threshold"), "5000")
    _type(qtbot, _row(dialog, name, "visualization.update_rate"), "30")
    _choose(_row(dialog, name, "visualization.colormap"), "magma")
    _toggle(qtbot, _row(dialog, name, "visualization.show_grid"), "Off")
    _toggle(qtbot, _row(dialog, name, "visualization.show_legend"), "Off")
    assert dialog._apply() is True
    assert {k: v for k, v, _s in env.prefs.calls} == {
        "visualization.auto_select": False,
        "visualization.default_type": "heatmap",
        "visualization.decimation_threshold": 5000,
        "visualization.update_rate": 30,
        "visualization.colormap": "magma",
        "visualization.show_grid": False,
        "visualization.show_legend": False,
    }
    assert {s for _k, _v, s in env.prefs.calls} == {USER}


def test_login_user_scope_writes_same_key(qtbot, env):
    dialog = _dialog(qtbot)
    _choose(_row(dialog, "login", "session_duration"), "8 hours")
    assert dialog._apply() is True
    assert env.prefs.calls == [("session_duration", 28800, USER)]


def test_login_page_fields_at_user_and_facility_only(qtbot, env):
    dialog = _dialog(qtbot)
    form = _form(dialog, "login")
    assert form.has_fields(USER)
    assert form.has_fields(FACILITY)
    assert not form.has_fields(BEAMLINE)
    assert not form.has_fields(LOCAL)
    viz = _form(dialog, "visualization")
    assert all(viz.has_fields(s) for s in (USER, BEAMLINE, FACILITY))
    assert not viz.has_fields(LOCAL)


def test_visualization_untouched_writes_nothing(qtbot, env):
    dialog = _dialog(qtbot)
    assert dialog._apply() is True
    assert env.prefs.calls == []


def test_visualization_number_fields_show_their_units(qtbot, env):
    from PySide6.QtWidgets import QLabel

    dialog = _dialog(qtbot)
    for key, unit in (("visualization.update_rate", "Hz"), ("visualization.decimation_threshold", "points")):
        editor = _row(dialog, "visualization", key).editor
        assert unit in [lbl.text() for lbl in editor.findChildren(QLabel) if not lbl.isHidden()]
