"""Devices page on the scope-aware form: connection scalars, CA tunnel, backend rows.

The old hand-built page (characterized in commit dfa756c4) wrote all five scalars unscoped
on every save and had a "Save to" combo for the rows. The form page writes only what the
user edited, at the scope shown; the rows editor writes that scope's own rows (overrides
of inherited rows included) and the exclusive restore list at the same scope.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QComboBox, QLineEdit, QMessageBox

from lightfall.devices.backend_types import startup
from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import (
    DEVICE_BACKENDS_KEY,
    EXCLUSIVE_RESTORE_KEY,
    BackendRow,
)
from lightfall.ui.preferences import device_rows, device_settings
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.scope_api import ScopeChoice
from tests.devices.backend_types.excl_type import ExclType, no_session_mode  # noqa: F401

MODE, TIMEOUT, STARTUP = "device_instantiate_mode", "device_connection_timeout", "device_connect_on_startup"
TUNNEL, GATEWAY = "ca_tunnel_enabled", "ca_tunnel_gateway"
SCALARS = (MODE, TIMEOUT, STARTUP)
USER, LOCAL, BRANCH, BEAMLINE = "user:u1", "local", "branch:7.0.1.1", "beamline:7.0.1"
WRITABLE = (USER, LOCAL, BRANCH)
ORDER = (BEAMLINE, BRANCH, LOCAL, USER)  # broadest first
LABELS = {BEAMLINE: "7.0.1 (beamline)", BRANCH: "7.0.1.1 (branch)", LOCAL: "This console", USER: "Me"}


def _row(type_id, name, enabled=True, config=None, rid=None):
    row = BackendRow(type_id, name, enabled=enabled, config=dict(config or {}))
    if rid:
        row.id = rid
    return row


class FakePrefs(QObject):
    """Layers per scope; union keys merge item by item, scalars take the narrowest layer."""

    scopes_changed = Signal(object)
    scope_info = SimpleNamespace(mode="server")

    def __init__(self):
        super().__init__()
        self.layers: dict = {}
        self.calls: list[tuple] = []

    def scope_choices(self):
        return [
            ScopeChoice(USER, "user", "User", None, ""),
            ScopeChoice(LOCAL, "local", "This console", None, ""),
            ScopeChoice(BRANCH, "branch", "Branch", "maintainer", ""),
            ScopeChoice(BEAMLINE, "beamline", "Beamline", "viewer", "Read-only"),
        ]

    def default_scope(self):
        return USER

    def allowed_at(self, key, scope):
        from lightfall.ui.preferences.scope_api import allowed_at

        return allowed_at(self, key, scope)

    def writable_at(self, key, scope):
        return scope in WRITABLE and self.allowed_at(key, scope)

    def lock_at(self, key, scope):
        return None

    def layer_value(self, key, scope):
        return self.layers.get((scope, key))

    def inherited_items(self, key, scope):
        out = {}
        for s in ORDER[: ORDER.index(scope)] if scope in ORDER else ():
            for rid, item in (self.layers.get((s, key)) or {}).items():
                out[rid] = (item, LABELS[s])
        return out

    def resolved_items(self, key):
        out = {}
        for s in ORDER:
            out.update(self.layers.get((s, key)) or {})
        return out

    def get(self, key, default=None):
        if key in (DEVICE_BACKENDS_KEY, EXCLUSIVE_RESTORE_KEY):
            return self.resolved_items(key) or default
        for s in reversed(ORDER):
            if (s, key) in self.layers:
                return self.layers[(s, key)]
        return default

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
def env(monkeypatch, no_session_mode):  # noqa: F811
    BackendTypeRegistry.reset()
    BackendTypeRegistry.get_instance().register(ExclType())
    monkeypatch.setattr(device_rows, "_device_backend_plugin_names", lambda: [])
    monkeypatch.setattr(device_settings, "_plan_active", lambda: False)
    applied: list = []
    monkeypatch.setattr(startup, "apply_backend_rows", lambda rows, ctx=None: applied.append((rows, ctx)))

    pages = [device_settings.DeviceSettingsPlugin()]
    prefs = FakePrefs()
    plugins = SimpleNamespace(
        get_ready_by_type=lambda _t: [SimpleNamespace(instance=p) for p in pages],
        get_all=lambda: [],
    )
    services = SimpleNamespace(get=lambda _cls: plugins)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance", classmethod(lambda cls: services)
    )
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: prefs)
    )
    boxes: list[str] = []
    infos: list[str] = []
    questions: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text, *a, **k: boxes.append(text))
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text, *a, **k: infos.append(text))

    def question(_p, _t, text, *a, **k):
        questions.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    yield SimpleNamespace(prefs=prefs, pages=pages, boxes=boxes, infos=infos, questions=questions,
                          applied=applied)
    BackendTypeRegistry.reset()


def _dialog(qtbot):
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    dialog.prompts = []
    dialog._prompt_restart = lambda: dialog.prompts.append(list(dialog._restart_reasons))
    return dialog


def _form(dialog) -> SettingsForm:
    return next(f for f in dialog.findChildren(SettingsForm) if f._plugin.name == "devices")


def _shown(dialog) -> set[str]:
    form = _form(dialog)
    return {r.key for r in form.findChildren(SettingRow) if r.isVisibleTo(form)}


def _row_of(dialog, key) -> SettingRow:
    return next(r for r in _form(dialog).findChildren(SettingRow) if r.key == key)


def _rows_editor(dialog):
    return _row_of(dialog, DEVICE_BACKENDS_KEY).editor


def _pick(dialog, key, text):
    box = _row_of(dialog, key).editor.findChild(QComboBox)
    index = box.findText(text)
    assert index >= 0, (key, text, [box.itemText(i) for i in range(box.count())])
    box.setCurrentIndex(index)


def _type(dialog, key, text):
    edit = _row_of(dialog, key).editor.findChild(QLineEdit)
    edit.setText(text)
    edit.textEdited.emit(text)


def test_specs_and_scopes(qapp):
    page = device_settings.DeviceSettingsPlugin()
    assert (page.name, page.display_name, page.category, page.priority) == ("devices", "Devices", "general", 20)
    specs = {s.key: s for s in page.specs()}
    assert set(specs) == set(SCALARS)
    assert (specs[MODE].type, specs[TIMEOUT].type, specs[STARTUP].type) == (str, float, bool)
    prefs = FakePrefs()
    for key in SCALARS:
        assert specs[key].scopes == {"beamline", "branch", "user"}
        assert prefs.allowed_at(key, LOCAL), key
        assert not prefs.allowed_at(key, "facility"), key
    for key in (TUNNEL, GATEWAY):
        assert prefs.allowed_at(key, LOCAL) and not prefs.allowed_at(key, USER)
    titles = [s.title for s in page.sections()]
    assert titles == ["Connection", "Remote access (CA tunnel)", "Backends"]


def test_user_scope_shows_connection_and_backends_not_tunnel(qtbot, env):
    dialog = _dialog(qtbot)
    shown = _shown(dialog)
    assert {*SCALARS, DEVICE_BACKENDS_KEY} <= shown
    assert TUNNEL not in shown and GATEWAY not in shown
    box = _row_of(dialog, MODE).editor.findChild(QComboBox)
    assert [box.itemText(i) for i in range(box.count())] == [
        "Not set", "Background (recommended)", "On demand (manual)", "Blocking (legacy)"]


def test_console_scope_shows_tunnel(qtbot, env):
    dialog = _dialog(qtbot)
    dialog.select_scope(LOCAL)
    shown = _shown(dialog)
    assert {*SCALARS, TUNNEL, GATEWAY, DEVICE_BACKENDS_KEY} <= shown
    gateway = _row_of(dialog, GATEWAY).editor.findChild(QLineEdit)
    assert gateway.placeholderText() == "localhost:5099"
    assert not gateway.isEnabled()
    _pick(dialog, TUNNEL, "On")
    assert gateway.isEnabled()
    _type(dialog, GATEWAY, "localhost:6000")
    assert dialog._apply() is True
    assert sorted(env.prefs.calls, key=str) == sorted(
        [(TUNNEL, True, LOCAL), (GATEWAY, "localhost:6000", LOCAL)], key=str)
    assert dialog.prompts  # tunnel settings need a restart


def test_untouched_page_writes_nothing(qtbot, env):
    a = _row("mock", "a", rid="a")
    env.prefs.layers[(BRANCH, DEVICE_BACKENDS_KEY)] = {"a": a.to_dict()}
    env.prefs.layers[(USER, TIMEOUT)] = 7.5
    dialog = _dialog(qtbot)
    assert dialog._apply() is True
    assert env.prefs.calls == []
    assert env.applied == []
    assert dialog.prompts == []


def test_edit_scalar_writes_only_that_key_at_scope(qtbot, env):
    dialog = _dialog(qtbot)
    _type(dialog, TIMEOUT, "2.5")
    assert dialog._apply() is True
    assert env.prefs.calls == [(TIMEOUT, 2.5, USER)]
    assert env.applied == []  # rows unchanged: no live apply

    dialog.select_scope(BRANCH)
    _pick(dialog, MODE, "On demand (manual)")
    assert dialog._apply() is True
    assert env.prefs.calls[-1] == (MODE, "none", BRANCH)


def test_scalars_at_user_write_the_old_values(qtbot, env):
    """The old page wrote these values (unscoped, every save); the form writes the edited ones."""
    dialog = _dialog(qtbot)
    _pick(dialog, MODE, "Background (recommended)")
    _type(dialog, TIMEOUT, "5")
    _pick(dialog, STARTUP, "On")
    assert dialog._apply() is True
    assert sorted(env.prefs.calls, key=str) == sorted(
        [(MODE, "background", USER), (TIMEOUT, 5.0, USER), (STARTUP, True, USER)], key=str)


def test_switch_off_inherited_row_writes_override_at_user(qtbot, env):
    a = _row("mock", "a", rid="a")
    b = _row("mock", "b", rid="b")
    env.prefs.layers[(BRANCH, DEVICE_BACKENDS_KEY)] = {"a": a.to_dict(), "b": b.to_dict()}
    dialog = _dialog(qtbot)
    _rows_editor(dialog).set_checked("a", False)
    assert dialog._apply() is True
    assert env.prefs.calls == [(DEVICE_BACKENDS_KEY, {"a": {**a.to_dict(), "enabled": False}}, USER)]
    # the branch layer is untouched; the live catalog gets the resolved rows
    assert env.prefs.layers[(BRANCH, DEVICE_BACKENDS_KEY)] == {"a": a.to_dict(), "b": b.to_dict()}
    [(rows, _ctx)] = env.applied
    assert [(r.id, r.enabled) for r in rows] == [("a", False), ("b", True)]


def test_exclusive_writes_rows_and_restore_at_console(qtbot, env):
    a = _row("mock", "a", rid="a")
    sim = _row("excl", "sim", enabled=False, config={"addr": "127.0.0.1"}, rid="sim")
    env.prefs.layers[(BRANCH, DEVICE_BACKENDS_KEY)] = {"a": a.to_dict()}
    env.prefs.layers[(LOCAL, DEVICE_BACKENDS_KEY)] = {"sim": sim.to_dict()}
    dialog = _dialog(qtbot)
    dialog.select_scope(LOCAL)
    _rows_editor(dialog).set_checked("sim", True)
    assert env.questions
    assert dialog._apply() is True
    assert sorted(env.prefs.calls, key=str) == sorted([
        (DEVICE_BACKENDS_KEY,
         {"a": {**a.to_dict(), "enabled": False}, "sim": {**sim.to_dict(), "enabled": True}}, LOCAL),
        (EXCLUSIVE_RESTORE_KEY, {"a": True}, LOCAL),
    ], key=str)
    assert env.applied == []  # entering an exclusive mode takes a restart
    assert dialog.prompts == [["Device backends: Lightfall needs to restart to enter Excl: 127.0.0.1."]]


def test_save_applies_resolved_rows_live(qtbot, env):
    a = _row("mock", "a", rid="a")
    env.prefs.layers[(BRANCH, DEVICE_BACKENDS_KEY)] = {"a": a.to_dict()}
    env.prefs.layers[(USER, TIMEOUT)] = 3.0
    dialog = _dialog(qtbot)
    editor = _rows_editor(dialog)
    editor.set_checked("a", False)
    assert dialog._apply() is True
    [(rows, ctx)] = env.applied
    assert [(r.id, r.enabled) for r in rows] == [("a", False)]
    assert ctx.connection_timeout == 3.0
    assert dialog.prompts == []


def test_no_live_apply_while_plan_runs_message_only(qtbot, env, monkeypatch):
    monkeypatch.setattr(device_settings, "_plan_active", lambda: True)
    a = _row("mock", "a", rid="a")
    sim = _row("excl", "sim", enabled=False, rid="sim", config={"addr": "127.0.0.1"})
    env.prefs.layers[(BRANCH, DEVICE_BACKENDS_KEY)] = {"a": a.to_dict(), "sim": sim.to_dict()}
    dialog = _dialog(qtbot)
    _rows_editor(dialog).set_checked("sim", True)  # would cross the mode boundary
    assert dialog._apply() is True
    assert env.applied == []
    assert env.infos == [
        "A plan is running, so the backend changes were saved but not applied. "
        "Save again once it finishes (or restart)."
    ]
    assert dialog.prompts == []


def test_crossing_mode_boundary_prompts_once_without_live_apply(qtbot, env):
    from lightfall.devices.backend_types import session

    sim = _row("excl", "sim", rid="sim", config={"addr": "127.0.0.1"})
    env.prefs.layers[(USER, DEVICE_BACKENDS_KEY)] = {"sim": sim.to_dict()}
    session.set_session_mode(session.SessionMode(row_id="sim", type_id="excl", label="Excl: 127.0.0.1",
                                                 env={"EPICS_CA_ADDR_LIST": "127.0.0.1"}))
    dialog = _dialog(qtbot)
    _rows_editor(dialog).set_checked("sim", False)
    assert dialog._apply() is True
    assert env.applied == []
    assert dialog.prompts == [["Device backends: Lightfall needs to restart to leave Excl: 127.0.0.1."]]


def test_validation_blocks_duplicate_names(qtbot, env):
    one = _row("mock", "same", rid="one")
    two = _row("mock", "Same", rid="two")
    env.prefs.layers[(USER, DEVICE_BACKENDS_KEY)] = {"one": one.to_dict(), "two": two.to_dict()}
    dialog = _dialog(qtbot)
    _rows_editor(dialog).set_checked("two", False)
    assert dialog._apply() is False
    assert env.prefs.calls == []
    assert any("Backend names must be unique: 'Same' is used twice" in b for b in env.boxes)


def test_validate_values_messages(qapp, monkeypatch):
    class Values:
        def __init__(self, value):
            self.value = value

        def get(self, key):
            return self.value if key == DEVICE_BACKENDS_KEY else None

    BackendTypeRegistry.reset()
    page = device_settings.DeviceSettingsPlugin()
    nameless = _row("mock", "", rid="x")
    assert page.validate_values(Values({"x": nameless.to_dict()})) == ["Every backend needs a name"]
    happi = _row("bcs", "h", rid="h", config={"host": ""})
    off = _row("bcs", "off", enabled=False, rid="o", config={"host": ""})
    errors = page.validate_values(Values({"h": happi.to_dict(), "o": off.to_dict()}))
    assert errors == ["h: Server host is required"]
    assert page.validate_values(Values(None)) == []
    BackendTypeRegistry.reset()
