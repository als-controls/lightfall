"""Tiled page: the keys it writes and where, the binding-derived beamline, the API key
and admin override visibility, validation and Test Connection."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QComboBox, QGroupBox, QLineEdit

from lightfall.scopes.keys import CONSOLE_WHEN_UNBOUND_KEYS, client_registry, console_only
from lightfall.ui.preferences import tiled_settings as mod
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.items import ActionItem, InfoItem
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.scope_api import ScopeChoice
from lightfall.ui.preferences.tiled_settings import TiledSettingsPlugin

USER, LOCAL, BRANCH, BEAMLINE = "user:u1", "local", "branch:7.0.1.1", "beamline:7.0.1"

# Same edits as on the old page (characterized there first), grouped by the scope the
# new page edits them at. The old page never wrote tiled_beamline.
EXPECTED_BEAMLINE = {
    "tiled_enabled": True,
    "tiled_url": "https://tiled.example.org",
    "tiled_auth_mode": "api_key",
    "tiled_alshub_url": "https://alshub.example.org",
}
EXPECTED_LOCAL = {"tiled_api_key": "s3cret-key"}
EXPECTED_USER = {
    "tiled_session_key_lifetime": 86400,
    "tiled_access_override_esaf_id": "BLS-00480-001",
    "tiled_access_override_start": "2026-04-26T18:00:00+00:00",
    "tiled_access_override_end": "2026-04-27T02:00:00+00:00",
}
OVERRIDE_KEYS = ("tiled_access_override_esaf_id", "tiled_access_override_start", "tiled_access_override_end")


class FakePrefs(QObject):
    """Scope rules from the real client registry; values from ``values``/``layers``."""

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
        return USER

    def console_bound(self):
        return self.bound

    def allowed_at(self, key, scope):
        if console_only(key):
            return scope == LOCAL
        if scope == LOCAL:
            return key in CONSOLE_WHEN_UNBOUND_KEYS
        return client_registry().get(key).allows(scope.split(":", 1)[0])

    def writable_at(self, key, scope):
        if key in CONSOLE_WHEN_UNBOUND_KEYS and scope == LOCAL:
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


class FakeService:
    def __init__(self, result=(True, "Connected")):
        self.result = result
        self.calls: list[tuple] = []
        self.thread = None

    def test_connection(self, url, api_key=None, auth_mode=None):
        self.calls.append((url, api_key, auth_mode))
        self.thread = threading.current_thread()
        return self.result


@pytest.fixture
def env(monkeypatch):
    prefs = FakePrefs()
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: prefs)
    )
    plugins = [TiledSettingsPlugin()]
    registry = SimpleNamespace(get_ready_by_type=lambda _t: [SimpleNamespace(instance=p) for p in plugins])
    services = SimpleNamespace(get=lambda cls, default=None: registry)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance", classmethod(lambda cls: services)
    )
    monkeypatch.setattr(TiledSettingsPlugin, "_user_has_admin_or_staff", lambda self: True)
    service = FakeService()
    monkeypatch.setattr(
        "lightfall.services.tiled_service.TiledService.get_instance", classmethod(lambda cls: service)
    )
    return SimpleNamespace(prefs=prefs, plugins=plugins, service=service)


def _dialog(qtbot, scope=None):
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    if scope is not None:
        dialog.select_scope(scope)
    return dialog


def _form(dialog) -> SettingsForm:
    return next(f for f in dialog.findChildren(SettingsForm) if f._plugin.name == "tiled")


def _rows(dialog) -> dict[str, SettingRow]:
    return {r.key: r for r in _form(dialog).findChildren(SettingRow)}


def _row(dialog, key) -> SettingRow:
    return _rows(dialog)[key]


def _visible(dialog, key) -> bool:
    return _row(dialog, key).isVisibleTo(_form(dialog))


def _choose(row, text):
    combo = row.editor.findChild(QComboBox)
    index = combo.findText(text)
    assert index >= 0, text
    combo.setCurrentIndex(index)


def _type(qtbot, row, text):
    line = row.editor.findChild(QLineEdit)
    line.clear()
    qtbot.keyClicks(line, text)


def _values(scope=LOCAL, dirty=(), **kw):
    return SimpleNamespace(scope=scope, effective=lambda key, default=None: kw.get(key, default),
                           get=lambda key: kw.get(key), is_dirty=lambda key: key in dirty)


# Characterization ------------------------------------------------------------------

def test_tiled_writes_same_keys(qtbot, env):
    env.prefs.values["tiled_auth_mode"] = "api_key"  # so the console's API key shows
    dialog = _dialog(qtbot, BEAMLINE)
    _choose(_row(dialog, "tiled_enabled"), "On")
    _type(qtbot, _row(dialog, "tiled_url"), "https://tiled.example.org")
    _choose(_row(dialog, "tiled_auth_mode"), "API Key")
    _type(qtbot, _row(dialog, "tiled_alshub_url"), "https://alshub.example.org")
    dialog.select_scope(LOCAL)
    _type(qtbot, _row(dialog, "tiled_api_key"), "s3cret-key")
    dialog.select_scope(USER)
    _choose(_row(dialog, "tiled_session_key_lifetime"), "1 day")
    _type(qtbot, _row(dialog, "tiled_access_override_esaf_id"), "BLS-00480-001")
    _type(qtbot, _row(dialog, "tiled_access_override_start"), "2026-04-26T18:00:00+00:00")
    _type(qtbot, _row(dialog, "tiled_access_override_end"), "2026-04-27T02:00:00+00:00")
    assert dialog._apply() is True
    by_scope: dict[str, dict] = {}
    for key, value, scope in env.prefs.calls:
        by_scope.setdefault(scope, {})[key] = value
    assert by_scope == {BEAMLINE: EXPECTED_BEAMLINE, LOCAL: EXPECTED_LOCAL, USER: EXPECTED_USER}


def test_untouched_tiled_page_writes_nothing(qtbot, env):
    dialog = _dialog(qtbot)
    assert dialog._apply() is True
    assert env.prefs.calls == []


# Where each key lives --------------------------------------------------------------

def test_tiled_enabled_absent_at_user_scope(qtbot, env):
    dialog = _dialog(qtbot, USER)
    assert set(_rows(dialog)) == {
        "tiled_url", "tiled_session_key_lifetime", "tiled_alshub_url", *OVERRIDE_KEYS,
    }
    dialog.select_scope(BEAMLINE)
    assert set(_rows(dialog)) == {
        "tiled_enabled", "tiled_url", "tiled_auth_mode", "tiled_beamline", "tiled_alshub_url",
    }
    dialog.select_scope(LOCAL)
    assert set(_rows(dialog)) == {"tiled_api_key", "tiled_beamline"}


def test_page_specs_declare_user_only_keys(env):
    reg = client_registry()
    for key in ("tiled_session_key_lifetime", *OVERRIDE_KEYS):
        assert reg.is_declared(key)
        assert set(reg.get(key).scopes) == {"user"}


def test_api_key_visible_only_when_effective_auth_is_api_key(qtbot, env):
    dialog = _dialog(qtbot, LOCAL)
    assert not _visible(dialog, "tiled_api_key")
    env.prefs.values["tiled_auth_mode"] = "api_key"
    dialog2 = _dialog(qtbot, LOCAL)
    assert _visible(dialog2, "tiled_api_key")
    assert mod._api_key_mode(_values(tiled_auth_mode="api_key"))
    for mode in ("", "none", "keycloak", None):
        assert not mod._api_key_mode(_values(tiled_auth_mode=mode))


def test_url_enabled_by_effective_tiled_enabled(qtbot, env):
    dialog = _dialog(qtbot, BEAMLINE)
    assert not _row(dialog, "tiled_url").editor.isEnabled()
    _choose(_row(dialog, "tiled_enabled"), "On")
    assert _row(dialog, "tiled_url").editor.isEnabled()
    env.prefs.values["tiled_enabled"] = True
    assert _row(_dialog(qtbot, USER), "tiled_url").editor.isEnabled()  # set by the site


def test_url_placeholder_is_the_default_server(qtbot, env):
    from lightfall.services.tiled_service import DEFAULT_TILED_URL

    line = _row(_dialog(qtbot), "tiled_url").editor.findChild(QLineEdit)
    assert line.placeholderText() == DEFAULT_TILED_URL
    assert line.text() == ""


def test_auth_mode_automatic_is_a_value(qtbot, env):
    dialog = _dialog(qtbot, BEAMLINE)
    _choose(_row(dialog, "tiled_auth_mode"), "Automatic (match sign-in)")
    assert dialog._apply() is True
    assert env.prefs.calls == [("tiled_auth_mode", "", BEAMLINE)]


def test_override_section_hidden_for_non_staff(qtbot, env, monkeypatch):
    monkeypatch.setattr(TiledSettingsPlugin, "_user_has_admin_or_staff", lambda self: False)
    dialog = _dialog(qtbot, USER)
    form = _form(dialog)
    for key in OVERRIDE_KEYS:
        assert not _visible(dialog, key)
    box = next(b for b in form.findChildren(QGroupBox) if b.title() == mod.OVERRIDE_SECTION)
    assert not box.isVisibleTo(form)


def test_override_section_shown_for_staff(qtbot, env):
    dialog = _dialog(qtbot, USER)
    form = _form(dialog)
    for key in OVERRIDE_KEYS:
        assert _visible(dialog, key)
    box = next(b for b in form.findChildren(QGroupBox) if b.title() == mod.OVERRIDE_SECTION)
    assert box.isVisibleTo(form)


def test_custom_lifetime_kept(qtbot, env):
    env.prefs.layers[(USER, "tiled_session_key_lifetime")] = 1800
    env.prefs.values["tiled_session_key_lifetime"] = 1800
    dialog = _dialog(qtbot, USER)
    combo = _row(dialog, "tiled_session_key_lifetime").editor.findChild(QComboBox)
    assert combo.currentText() == "1800 seconds (custom)"
    assert dialog._apply() is True
    assert env.prefs.calls == []


# Binding-derived beamline ----------------------------------------------------------

def _binding_infos(dialog) -> list[InfoItem]:
    return [i for i in _form(dialog).findChildren(InfoItem) if i.info is mod._BEAMLINE_BINDING]


def test_beamline_editable_at_console_when_unbound(qtbot, env):
    dialog = _dialog(qtbot, LOCAL)
    assert _visible(dialog, "tiled_beamline")
    assert not _binding_infos(dialog)[0].isVisibleTo(_form(dialog))  # empty line hidden
    _type(qtbot, _row(dialog, "tiled_beamline"), "7.3.3")
    assert dialog._apply() is True
    assert env.prefs.calls == [("tiled_beamline", "7.3.3", LOCAL)]


def test_beamline_from_binding_when_bound(qtbot, env):
    env.prefs.bound = True
    env.prefs.values["tiled_beamline"] = "7.0.1.1"
    dialog = _dialog(qtbot, LOCAL)
    assert not _visible(dialog, "tiled_beamline")
    info = _binding_infos(dialog)[0]
    assert info.isVisibleTo(_form(dialog))
    assert info.text() == "From this console's binding: 7.0.1.1"
    dialog.select_scope(BRANCH)
    assert _visible(dialog, "tiled_beamline")
    assert _binding_infos(dialog) == []
    _type(qtbot, _row(dialog, "tiled_beamline"), "7.0.1.2")
    assert dialog._apply() is True
    assert env.prefs.calls == [("tiled_beamline", "7.0.1.2", BRANCH)]


# Validation ------------------------------------------------------------------------

def test_tiled_requires_valid_url_when_enabled(env):
    page = TiledSettingsPlugin()
    required = "Tiled server URL is required when enabled"
    bad = "Tiled server URL must start with http:// or https://"
    on = {"tiled_enabled": True}
    assert page.validate_values(_values(BEAMLINE, dirty={"tiled_enabled"}, **on)) == [required]
    assert page.validate_values(_values(BEAMLINE, dirty={"tiled_url"}, tiled_url="", **on)) == [required]
    assert page.validate_values(_values(USER, dirty={"tiled_url"}, tiled_url="ftp://x", **on)) == [bad]
    assert page.validate_values(_values(USER, dirty={"tiled_url"}, tiled_url="http://", **on)) == [bad]
    assert page.validate_values(
        _values(BEAMLINE, dirty={"tiled_enabled"}, tiled_url="https://tiled.example.org", **on)
    ) == []
    # Disabled: any URL is fine.
    assert page.validate_values(_values(BEAMLINE, dirty={"tiled_url"}, tiled_url="")) == []


def test_validation_ignores_other_scopes_state(env):
    page = TiledSettingsPlugin()
    on_no_url = {"tiled_enabled": True, "tiled_url": ""}
    # Enabled with no URL somewhere else: an unrelated edit here is not blocked.
    assert page.validate_values(_values(BEAMLINE, dirty={"tiled_alshub_url"}, **on_no_url)) == []
    assert page.validate_values(_values(USER, dirty={"tiled_session_key_lifetime"}, **on_no_url)) == []
    # "This console" has no URL or enable field.
    assert page.validate_values(_values(LOCAL, dirty={"tiled_api_key"}, **on_no_url)) == []


def test_enabling_without_url_blocks_the_save(qtbot, env):
    dialog = _dialog(qtbot, BEAMLINE)
    _choose(_row(dialog, "tiled_enabled"), "On")
    assert dialog._validation_errors() == [
        "Tiled Data Catalog (Beamline): Tiled server URL is required when enabled"
    ]


# Test Connection -------------------------------------------------------------------

def test_test_connection_uses_effective_values(env):
    values = _values(tiled_url=" https://t.example ", tiled_api_key=" k ", tiled_auth_mode="api_key")
    assert mod._test_connection(values) == "Connected!"
    assert env.service.calls == [("https://t.example", "k", "api_key")]
    env.service.calls.clear()
    assert mod._test_connection(_values(tiled_url="https://t.example")) == "Connected!"
    assert env.service.calls == [("https://t.example", None, "")]
    with pytest.raises(RuntimeError, match="Enter a URL first"):
        mod._test_connection(_values(tiled_url=""))
    env.service.result = (False, "401 Unauthorized")
    with pytest.raises(RuntimeError, match="401 Unauthorized"):
        mod._test_connection(_values(tiled_url="https://t.example"))


def test_test_connection_runs_in_background(qtbot, env):
    env.prefs.values.update(tiled_enabled=True, tiled_url="https://site.example")
    dialog = _dialog(qtbot, USER)
    item = _form(dialog).findChild(ActionItem)
    assert item.button.isEnabled()
    item.button.click()
    qtbot.waitUntil(lambda: item.status.text() == "Connected!", timeout=5000)
    assert env.service.calls == [("https://site.example", None, "")]
    assert env.service.thread is not threading.main_thread()


def test_test_connection_disabled_when_tiled_off(qtbot, env):
    item = _form(_dialog(qtbot, USER)).findChild(ActionItem)
    assert not item.button.isEnabled()


# Other modules' imports ------------------------------------------------------------

def test_access_override_from_prefs(env):
    prefs = env.prefs
    assert mod.access_override_from_prefs(prefs) is None
    prefs.values.update({
        "tiled_access_override_esaf_id": "BLS-1",
        "tiled_access_override_start": "2026-04-26T18:00:00+00:00",
        "tiled_access_override_end": "2026-04-27T02:00:00+00:00",
    })
    override = mod.access_override_from_prefs(prefs)
    assert override.esaf_id == "BLS-1" and override.end.hour == 2
    prefs.values["tiled_access_override_end"] = "not a date"
    assert mod.access_override_from_prefs(prefs) is None
