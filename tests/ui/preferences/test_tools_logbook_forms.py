"""External Tools and Logbook pages: the keys they write at "This console"."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QComboBox, QLineEdit

from lightfall.scopes.keys import console_only
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.editor_settings import ExternalToolsSettingsPlugin
from lightfall.ui.preferences.form.items import ActionItem, InfoItem
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.logbook_settings import LogbookSettingsPlugin
from lightfall.ui.preferences.scope_api import ScopeChoice

USER, LOCAL, BEAMLINE, FACILITY = "user:u1", "local", "beamline:7.0.1", "facility"

EXPECTED_TOOLS = {"code_editor": "pycharm", "suppress_pycharm_warning": True}
EXPECTED_LOGBOOK = {
    "logbook_enabled": True,
    "logbook_url": "http://logbook.example.org:9000",
    "logbook_offline_only": True,
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
    plugins = [ExternalToolsSettingsPlugin(), LogbookSettingsPlugin()]
    prefs = FakePrefs()
    registry = SimpleNamespace(get_ready_by_type=lambda _t: [SimpleNamespace(instance=p) for p in plugins])
    services = SimpleNamespace(get=lambda _cls: registry)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance", classmethod(lambda cls: services)
    )
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: prefs)
    )
    monkeypatch.setattr(
        "lightfall.ui.preferences.editor_settings.is_editor_available", lambda editor: True
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


def _info_text(form) -> str:
    return form.findChild(InfoItem).text()


def _values(**kw):
    return SimpleNamespace(effective=lambda key, default=None: kw.get(key, default))


def test_external_tools_writes_same_keys(qtbot, env):
    dialog = _dialog(qtbot)
    _choose(_row(dialog, "external_tools", "code_editor"), "PyCharm")
    _toggle(_row(dialog, "external_tools", "suppress_pycharm_warning"), "On")
    assert dialog._apply() is True
    assert {k: v for k, v, _s in env.prefs.calls} == EXPECTED_TOOLS
    assert {s for _k, _v, s in env.prefs.calls} == {LOCAL}


def test_logbook_writes_same_keys(qtbot, env):
    dialog = _dialog(qtbot)
    _toggle(_row(dialog, "logbook", "logbook_enabled"), "On")
    _type(qtbot, _row(dialog, "logbook", "logbook_url"), "http://logbook.example.org:9000")
    _toggle(_row(dialog, "logbook", "logbook_offline_only"), "On")
    assert dialog._apply() is True
    assert {k: v for k, v, _s in env.prefs.calls} == EXPECTED_LOGBOOK
    assert {s for _k, _v, s in env.prefs.calls} == {LOCAL}


def test_tools_logbook_only_at_console(qtbot, env):
    dialog = _dialog(qtbot)
    for name in ("external_tools", "logbook"):
        form = _form(dialog, name)
        assert form.has_fields(LOCAL)
        for scope in (USER, BEAMLINE, FACILITY):
            assert not form.has_fields(scope)


def test_logbook_requires_url_when_enabled(qtbot, env):
    page = LogbookSettingsPlugin()
    message = "Logbook server URL is required when sync is enabled"
    assert page.validate_values(_values(logbook_enabled=True, logbook_url="  ")) == [message]
    assert page.validate_values(_values(logbook_url="")) == [message]  # enabled by default
    assert page.validate_values(_values(logbook_enabled=False, logbook_url="")) == []
    assert page.validate_values(_values(logbook_url="", logbook_offline_only=True)) == []
    assert page.validate_values(_values(logbook_url="http://x:1")) == []


def test_logbook_test_connection_runs_in_background(qtbot, env, monkeypatch):
    import threading

    import httpx

    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url):
            seen["url"] = url
            seen["thread"] = threading.current_thread()
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    dialog = _dialog(qtbot)
    item = _form(dialog, "logbook").findChild(ActionItem)
    assert item.button.isEnabled()
    item.button.click()
    qtbot.waitUntil(lambda: "reachable" in item.status.text(), timeout=5000)
    assert seen["url"].endswith("/health")
    assert seen["url"].startswith("http://bcglucidlogbook")  # default URL when nothing is set
    assert seen["thread"] is not threading.main_thread()


def test_logbook_test_connection_reports_http_error(qtbot, env, monkeypatch):
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url):
            return SimpleNamespace(status_code=503)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    dialog = _dialog(qtbot)
    item = _form(dialog, "logbook").findChild(ActionItem)
    item.button.click()
    qtbot.waitUntil(lambda: item.status.text() == "HTTP 503", timeout=5000)


def test_logbook_url_and_test_follow_enabled_toggle(qtbot, env):
    dialog = _dialog(qtbot)
    url_row = _row(dialog, "logbook", "logbook_url")
    item = _form(dialog, "logbook").findChild(ActionItem)
    assert url_row.editor.isEnabled() and item.button.isEnabled()
    _toggle(_row(dialog, "logbook", "logbook_enabled"), "Off")
    assert not url_row.editor.isEnabled()
    assert not item.button.isEnabled()
    assert url_row.editor.findChild(QLineEdit).placeholderText().startswith("http://bcglucidlogbook")


def test_suppress_warning_enabled_only_for_pycharm(qtbot, env):
    dialog = _dialog(qtbot)
    suppress = _row(dialog, "external_tools", "suppress_pycharm_warning")
    assert not suppress.editor.isEnabled()
    _choose(_row(dialog, "external_tools", "code_editor"), "PyCharm")
    assert suppress.editor.isEnabled()
    _choose(_row(dialog, "external_tools", "code_editor"), "Visual Studio Code")
    assert not suppress.editor.isEnabled()


def test_protocol_status_follows_effective_editor(qtbot, env, monkeypatch):
    monkeypatch.setattr(
        "lightfall.ui.preferences.editor_settings.is_editor_available",
        lambda editor: editor.value == "vscode",
    )
    dialog = _dialog(qtbot)
    form = _form(dialog, "external_tools")
    assert "vscode:// protocol is registered" in _info_text(form)
    _choose(_row(dialog, "external_tools", "code_editor"), "PyCharm")
    assert "protocol not found" in _info_text(form)


def test_untouched_pages_write_nothing(qtbot, env):
    dialog = _dialog(qtbot)
    assert dialog._apply() is True
    assert env.prefs.calls == []
