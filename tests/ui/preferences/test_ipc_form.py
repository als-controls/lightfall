"""IPC page: keys written at "This console", the binding-derived topic prefix,
local-server gating, validation, Test Connection and the trusted-apps list."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QComboBox, QLineEdit, QListWidget, QPushButton

from lightfall.ipc.trust import TrustManager
from lightfall.scopes.keys import console_only
from lightfall.ui.preferences import ipc_settings as mod
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.items import ActionItem, InfoItem
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.ipc_settings import IPCSettingsPlugin, TrustedAppsWidget
from lightfall.ui.preferences.scope_api import ScopeChoice

USER, LOCAL, BRANCH, BEAMLINE = "user:u1", "local", "branch:7.0.1.1", "beamline:7.0.1"
PREFIX = "ipc_topic_prefix"

# Same edits as the old page; it never wrote ipc_topic_prefix.
EXPECTED_IPC = {
    "ipc_nats_url": "nats://broker.example.org:4222",
    "ipc_display_name": "CMS Hutch",
    "ipc_use_local_nats": True,
    "ipc_local_nats_port": 4299,
}


class FakePrefs(QObject):
    scopes_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.layers: dict = {}
        self.values: dict = {}
        self.calls: list[tuple] = []
        self.bound = False

    def scope_choices(self):
        return [
            ScopeChoice(USER, "user", "User", None, ""),
            ScopeChoice(LOCAL, "local", "This console", None, ""),
            ScopeChoice(BRANCH, "branch", "Branch", "operator", ""),
            ScopeChoice(BEAMLINE, "beamline", "Beamline", "operator", ""),
        ]

    def default_scope(self):
        return LOCAL

    def console_bound(self):
        return self.bound

    def allowed_at(self, key, scope):
        if key == PREFIX:
            return scope in (LOCAL, BRANCH, BEAMLINE)
        return scope == LOCAL and console_only(key)

    def writable_at(self, key, scope):
        if key == PREFIX and scope == LOCAL:
            return not self.bound
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


class FakeTrust:
    def __init__(self, apps):
        self.apps = set(apps)
        self.revoked: list[str] = []

    @property
    def trusted_apps(self):
        return set(self.apps)

    def revoke(self, name):
        self.revoked.append(name)
        self.apps.discard(name)


@pytest.fixture
def env(monkeypatch):
    plugins = [IPCSettingsPlugin()]
    prefs = FakePrefs()
    trust = FakeTrust({"beta-app", "alpha-app"})
    registry = SimpleNamespace(get_ready_by_type=lambda _t: [SimpleNamespace(instance=p) for p in plugins])
    services = SimpleNamespace(get=lambda cls, default=None: trust if cls is TrustManager else registry)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance", classmethod(lambda cls: services)
    )
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: prefs)
    )
    monkeypatch.setattr(mod, "resolve_nats_binary", lambda: "/x/nats-server")
    monkeypatch.setattr(mod, "nats_binary_version", lambda p: "2.14.2")
    return SimpleNamespace(prefs=prefs, plugins=plugins, trust=trust)


def _dialog(qtbot):
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    return dialog


def _form(dialog) -> SettingsForm:
    return next(f for f in dialog.findChildren(SettingsForm) if f._plugin.name == "ipc")


def _row(dialog, key) -> SettingRow:
    return next(r for r in _form(dialog).findChildren(SettingRow) if r.key == key)


def _toggle(row, text):
    combo = row.editor.findChild(QComboBox)
    combo.setCurrentIndex(combo.findText(text))


def _type(qtbot, row, text):
    line = row.editor.findChild(QLineEdit)
    line.clear()
    qtbot.keyClicks(line, text)


def _info_texts(dialog) -> list[str]:
    return [i.text() for i in _form(dialog).findChildren(InfoItem)]


def _values(**kw):
    return SimpleNamespace(scope=LOCAL, effective=lambda key, default=None: kw.get(key, default),
                           get=lambda key: kw.get(key))


def test_ipc_writes_same_keys(qtbot, env):
    dialog = _dialog(qtbot)
    _type(qtbot, _row(dialog, "ipc_nats_url"), "nats://broker.example.org:4222")
    _type(qtbot, _row(dialog, "ipc_display_name"), "CMS Hutch")
    _toggle(_row(dialog, "ipc_use_local_nats"), "On")
    _type(qtbot, _row(dialog, "ipc_local_nats_port"), "4299")
    assert dialog._apply() is True
    assert {k: v for k, v, _s in env.prefs.calls} == EXPECTED_IPC
    assert {s for _k, _v, s in env.prefs.calls} == {LOCAL}


def test_untouched_ipc_page_writes_nothing(qtbot, env):
    dialog = _dialog(qtbot)
    assert dialog._apply() is True
    assert env.prefs.calls == []


def test_ipc_only_prefix_outside_console(qtbot, env):
    form = _form(_dialog(qtbot))
    assert form.has_fields(LOCAL)
    assert not form.has_fields(USER)
    assert form.has_fields(BRANCH)


def test_ipc_topic_prefix_console_field_only_when_unbound(qtbot, env):
    dialog = _dialog(qtbot)
    form = _form(dialog)
    row = _row(dialog, PREFIX)
    assert row.isVisibleTo(form)
    binding = [i for i in form.findChildren(InfoItem) if i.info.text == IPCSettingsPlugin._prefix_binding]
    assert len(binding) == 1 and not binding[0].isVisibleTo(form)  # empty line hidden
    _type(qtbot, row, "dev.me")
    assert dialog._apply() is True
    assert env.prefs.calls == [(PREFIX, "dev.me", LOCAL)]

    env.prefs.calls.clear()
    env.prefs.bound = True
    env.prefs.values[PREFIX] = "als.7011"
    dialog2 = _dialog(qtbot)
    form2 = _form(dialog2)
    assert not _row(dialog2, PREFIX).isVisibleTo(form2)
    binding = [i for i in form2.findChildren(InfoItem) if i.info.text == IPCSettingsPlugin._prefix_binding]
    assert binding[0].isVisibleTo(form2)
    assert binding[0].text() == "From this console's binding: als.7011"

    # At Branch the prefix is a plain field, and the console-only items are gone.
    dialog2.select_scope(BRANCH)
    row = _row(dialog2, PREFIX)
    assert row.isVisibleTo(_form(dialog2))
    assert [r.key for r in _form(dialog2).findChildren(SettingRow)] == [PREFIX]
    assert _info_texts(dialog2) == []
    assert _form(dialog2).findChild(ActionItem) is None
    assert _form(dialog2).findChild(TrustedAppsWidget) is None
    _type(qtbot, row, "als.7011.1")
    assert dialog2._apply() is True
    assert env.prefs.calls == [(PREFIX, "als.7011.1", BRANCH)]


def test_url_disabled_when_local_nats(qtbot, env):
    dialog = _dialog(qtbot)
    url = _row(dialog, "ipc_nats_url")
    port = _row(dialog, "ipc_local_nats_port")
    assert url.editor.isEnabled()
    assert not port.editor.isEnabled()
    _toggle(_row(dialog, "ipc_use_local_nats"), "On")
    assert not url.editor.isEnabled()
    assert port.editor.isEnabled()
    _toggle(_row(dialog, "ipc_use_local_nats"), "Off")
    assert url.editor.isEnabled()
    assert not port.editor.isEnabled()


def test_url_placeholder_is_the_default_broker(qtbot, env):
    from lightfall.ipc.defaults import DEFAULT_IPC_NATS_URL

    line = _row(_dialog(qtbot), "ipc_nats_url").editor.findChild(QLineEdit)
    assert line.placeholderText() == DEFAULT_IPC_NATS_URL


def test_binary_status_info(qtbot, env):
    assert "v2.14.2 — /x/nats-server" in _info_texts(_dialog(qtbot))


def test_local_controls_disabled_without_binary(qtbot, env, monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "resolve_nats_binary", lambda: calls.append(1))
    dialog = _dialog(qtbot)
    assert not _row(dialog, "ipc_use_local_nats").editor.isEnabled()
    assert not _row(dialog, "ipc_local_nats_port").editor.isEnabled()
    assert _row(dialog, "ipc_nats_url").editor.isEnabled()
    assert any(t.startswith("nats-server not found") for t in _info_texts(dialog))
    n = len(calls)
    _type(qtbot, _row(dialog, "ipc_display_name"), "abc")  # predicates re-run, no new probe
    assert len(calls) == n


def test_stale_local_setting_can_be_turned_off_without_binary(qtbot, env, monkeypatch):
    monkeypatch.setattr(mod, "resolve_nats_binary", lambda: None)
    env.prefs.layers[(LOCAL, "ipc_use_local_nats")] = True
    env.prefs.values["ipc_use_local_nats"] = True
    dialog = _dialog(qtbot)
    toggle = _row(dialog, "ipc_use_local_nats")
    assert toggle.editor.isEnabled()
    _toggle(toggle, "Off")
    assert _row(dialog, "ipc_nats_url").editor.isEnabled()
    assert dialog._apply() is True
    assert env.prefs.calls == [("ipc_use_local_nats", False, LOCAL)]


def test_ipc_validation(env, monkeypatch):
    page = IPCSettingsPlugin()
    page.sections()
    assert page.validate_values(_values()) == []
    assert page.validate_values(_values(ipc_nats_url="nats://h:4222")) == []
    assert page.validate_values(_values(ipc_nats_url="")) == []
    assert page.validate_values(_values(ipc_nats_url="http://h:4222")) == [
        "NATS server URL must start with 'nats://' (or leave empty to disable IPC)"
    ]
    # Local server: the URL is not used, the port must be in range.
    assert page.validate_values(_values(ipc_use_local_nats=True, ipc_nats_url="http://x")) == []
    assert page.validate_values(_values(ipc_use_local_nats=True, ipc_local_nats_port=0)) == [
        "Local NATS port must be between 1 and 65535"
    ]
    monkeypatch.setattr(mod, "resolve_nats_binary", lambda: None)
    page.sections()
    assert page.validate_values(_values(ipc_use_local_nats=True)) == [
        "nats-server not found (install it or `pip install lightfall[local-nats]`)"
    ]


def test_trusted_apps_list_populated_and_revoke(qtbot, env):
    dialog = _dialog(qtbot)
    widget = _form(dialog).findChild(TrustedAppsWidget)
    lst = widget.findChild(QListWidget)
    assert [lst.item(i).text() for i in range(lst.count())] == ["alpha-app", "beta-app"]
    lst.setCurrentRow(1)
    widget.findChild(QPushButton).click()
    assert env.trust.revoked == ["beta-app"]  # immediately, not on Save
    assert [lst.item(i).text() for i in range(lst.count())] == ["alpha-app"]
    assert env.prefs.calls == []


def test_trusted_apps_without_trust_manager(qtbot):
    widget = TrustedAppsWidget(None)
    qtbot.addWidget(widget)
    assert widget.findChild(QListWidget).count() == 0
    assert not widget.findChild(QPushButton).isEnabled()


def test_test_connection_probes_url(env, monkeypatch):
    seen = {}

    def probe(host, port, timeout=5.0):
        seen["addr"] = (host, port)
        return {"version": "2.10.1"}

    monkeypatch.setattr(mod, "probe_nats", probe)
    assert mod._test_connection(_values(ipc_nats_url="nats://broker:4333")) == "Connected — NATS v2.10.1"
    assert seen["addr"] == ("broker", 4333)
    with pytest.raises(RuntimeError, match="No URL configured"):
        mod._test_connection(_values(ipc_nats_url=""))
    monkeypatch.setattr(mod, "probe_nats", lambda h, p, timeout=5.0: None)
    with pytest.raises(RuntimeError, match="Could not reach a NATS server"):
        mod._test_connection(_values(ipc_nats_url="nats://broker"))


def test_test_connection_runs_in_background(qtbot, env, monkeypatch):
    import threading

    seen = {}

    def probe(host, port, timeout=5.0):
        seen["thread"] = threading.current_thread()
        return {"version": "2.10.1"}

    monkeypatch.setattr(mod, "probe_nats", probe)
    item = _form(_dialog(qtbot)).findChild(ActionItem)
    item.button.click()
    qtbot.waitUntil(lambda: item.status.text() == "Connected — NATS v2.10.1", timeout=5000)
    assert seen["thread"] is not threading.main_thread()
