"""Proxy page: the keys it writes at \"This console\", enablement, validation, restart."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QComboBox, QLineEdit

from lightfall.scopes.keys import console_only
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.items import ActionItem
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.proxy_settings import ProxySettingsPlugin
from lightfall.ui.preferences.scope_api import ScopeChoice

USER, LOCAL, BEAMLINE, FACILITY = "user:u1", "local", "beamline:7.0.1", "facility"

EXPECTED_PROXY = {
    "proxy_enabled": True,
    "proxy_type": "http",
    "proxy_host": "proxy.example.org",
    "proxy_port": 8080,
    "proxy_auto_detect": True,
}


class FakePrefs(QObject):
    scopes_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.layers: dict = {}
        self.values: dict = {}
        self.calls: list[tuple] = []

    def scope_choices(self):
        return [
            ScopeChoice(USER, "user", "User", None, ""),
            ScopeChoice(LOCAL, "local", "This console", None, ""),
            ScopeChoice(BEAMLINE, "beamline", "Beamline", "operator", ""),
            ScopeChoice(FACILITY, "facility", "Facility", "maintainer", ""),
        ]

    def default_scope(self):
        return LOCAL

    def allowed_at(self, key, scope):
        return scope == LOCAL and console_only(key)

    def writable_at(self, key, scope):
        return self.allowed_at(key, scope)

    def lock_at(self, key, scope):
        return None

    def layer_value(self, key, scope):
        return self.layers.get((scope, key))

    def get(self, key, default=None):
        return self.values.get(key, default)

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
    plugins = [ProxySettingsPlugin()]
    prefs = FakePrefs()
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


def _toggle(row, text):
    combo = row.editor.findChild(QComboBox)
    combo.setCurrentIndex(combo.findText(text))


def _choose(row, text):
    combo = row.editor._combo
    combo.setCurrentIndex(combo.findText(text))


def _type(qtbot, row, text):
    line = row.editor.findChild(QLineEdit)
    line.clear()
    qtbot.keyClicks(line, text)


def _values(**kw):
    return SimpleNamespace(effective=lambda key, default=None: kw.get(key, default), is_dirty=lambda key: False)


def _fill(qtbot, dialog):
    _toggle(_row(dialog, "proxy", "proxy_enabled"), "On")
    _choose(_row(dialog, "proxy", "proxy_type"), "HTTP")
    _type(qtbot, _row(dialog, "proxy", "proxy_host"), "proxy.example.org")
    _type(qtbot, _row(dialog, "proxy", "proxy_port"), "8080")
    _toggle(_row(dialog, "proxy", "proxy_auto_detect"), "On")


def test_proxy_writes_same_keys(qtbot, env):
    dialog = _dialog(qtbot)
    _fill(qtbot, dialog)
    dialog._prompt_restart = lambda: None
    assert dialog._apply() is True
    assert {k: v for k, v, _s in env.prefs.calls} == EXPECTED_PROXY
    assert {s for _k, _v, s in env.prefs.calls} == {LOCAL}


def test_proxy_only_at_console(qtbot, env):
    form = _form(_dialog(qtbot), "proxy")
    assert form.has_fields(LOCAL)
    for scope in (USER, BEAMLINE, FACILITY):
        assert not form.has_fields(scope)


def test_auto_detect_enables_connection_fields(qtbot, env):
    dialog = _dialog(qtbot)
    keys = ("proxy_type", "proxy_host", "proxy_port")
    item = _form(dialog, "proxy").findChild(ActionItem)
    assert not any(_row(dialog, "proxy", k).editor.isEnabled() for k in keys)
    assert not item.button.isEnabled()
    _toggle(_row(dialog, "proxy", "proxy_auto_detect"), "On")
    assert all(_row(dialog, "proxy", k).editor.isEnabled() for k in keys)
    assert item.button.isEnabled()
    _toggle(_row(dialog, "proxy", "proxy_auto_detect"), "Off")
    assert not any(_row(dialog, "proxy", k).editor.isEnabled() for k in keys)
    _toggle(_row(dialog, "proxy", "proxy_enabled"), "On")
    assert all(_row(dialog, "proxy", k).editor.isEnabled() for k in keys)
    assert item.button.isEnabled()


HOST_REQUIRED = "Proxy host is required when the proxy or auto-detect is enabled"


def test_proxy_host_required_when_enabled(qtbot, env):
    page = ProxySettingsPlugin()
    message = HOST_REQUIRED
    assert page.validate_values(_values(proxy_enabled=True, proxy_host="  ")) == [message]
    assert page.validate_values(_values(proxy_enabled=True, proxy_host="")) == [message]
    assert page.validate_values(_values(proxy_enabled=True)) == []  # unset -> localhost
    assert page.validate_values(_values(proxy_enabled=False, proxy_host="")) == []
    assert page.validate_values(_values(proxy_enabled=True, proxy_host="h")) == []
    assert page.validate_values(_values(proxy_auto_detect=True, proxy_host=" ")) == [message]
    assert page.validate_values(_values(proxy_auto_detect=True)) == []


def test_blank_host_with_auto_detect_blocks_apply(qtbot, env, monkeypatch):
    warnings = []
    monkeypatch.setattr(
        "lightfall.ui.preferences.dialog.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    dialog = _dialog(qtbot)
    dialog._prompt_restart = lambda: None
    _toggle(_row(dialog, "proxy", "proxy_auto_detect"), "On")
    _type(qtbot, _row(dialog, "proxy", "proxy_host"), "h")  # then emptied: "" is a value, not unset
    qtbot.keyClick(_row(dialog, "proxy", "proxy_host").editor.findChild(QLineEdit), Qt.Key.Key_Backspace)
    assert dialog._apply() is False
    assert len(warnings) == 1 and HOST_REQUIRED in warnings[0]
    assert env.prefs.calls == []


def test_unset_host_with_auto_detect_saves(qtbot, env, monkeypatch):
    monkeypatch.setattr(
        "lightfall.ui.preferences.dialog.QMessageBox.warning",
        lambda *_a: pytest.fail("unexpected validation warning"),
    )
    dialog = _dialog(qtbot)
    dialog._prompt_restart = lambda: None
    _toggle(_row(dialog, "proxy", "proxy_auto_detect"), "On")
    assert dialog._apply() is True
    assert env.prefs.calls == [("proxy_auto_detect", True, LOCAL)]


def test_proxy_restart_prompt_once(qtbot, env):
    dialog = _dialog(qtbot)
    prompts = []
    dialog._prompt_restart = lambda: prompts.append(1)
    _fill(qtbot, dialog)
    assert dialog._apply() is True
    assert prompts == [1]


def test_auto_detect_alone_needs_no_restart(qtbot, env):
    dialog = _dialog(qtbot)
    prompts = []
    dialog._prompt_restart = lambda: prompts.append(1)
    _toggle(_row(dialog, "proxy", "proxy_auto_detect"), "On")
    assert dialog._apply() is True
    assert prompts == []


def test_proxy_test_connection_runs_in_background(qtbot, env, monkeypatch):
    import socket
    import threading

    seen = {}

    class FakeSocket:
        def __init__(self, *a):
            pass

        def settimeout(self, t):
            pass

        def connect_ex(self, addr):
            seen["addr"] = addr
            seen["thread"] = threading.current_thread()
            return 0

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", FakeSocket)
    dialog = _dialog(qtbot)
    _toggle(_row(dialog, "proxy", "proxy_enabled"), "On")
    item = _form(dialog, "proxy").findChild(ActionItem)
    item.button.click()
    qtbot.waitUntil(lambda: item.status.text() == "Connected!", timeout=5000)
    assert seen["addr"] == ("localhost", 1080)
    assert seen["thread"] is not threading.main_thread()


def test_proxy_test_connection_reports_refused(qtbot, env, monkeypatch):
    import socket

    class FakeSocket:
        def __init__(self, *a):
            pass

        def settimeout(self, t):
            pass

        def connect_ex(self, addr):
            return 111

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", FakeSocket)
    dialog = _dialog(qtbot)
    _toggle(_row(dialog, "proxy", "proxy_enabled"), "On")
    item = _form(dialog, "proxy").findChild(ActionItem)
    item.button.click()
    qtbot.waitUntil(lambda: item.status.text() == "Connection refused (error 111)", timeout=5000)


def test_untouched_proxy_page_writes_nothing(qtbot, env):
    dialog = _dialog(qtbot)
    assert dialog._apply() is True
    assert env.prefs.calls == []
