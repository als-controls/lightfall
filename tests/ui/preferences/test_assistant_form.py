"""Settings -> Assistant on the scope-aware form.

The old hand-built page (characterized in commit ea1508d3) wrote all fifteen keys unscoped
on every save. The form page writes only what the user edited, at the scope shown; the
API key and the agy path live at This console only.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication, QComboBox, QGroupBox, QLineEdit, QMessageBox

from lightfall.agents.backends import info as backend_info_mod
from lightfall.agents.backends.info import AuthStatus, ModelOption
from lightfall.scopes.keys import console_only
from lightfall.ui.preferences import assistant_editors
from lightfall.ui.preferences import claude_settings as cs
from lightfall.ui.preferences.dialog import PreferencesDialog
from lightfall.ui.preferences.form.items import ActionItem, InfoItem
from lightfall.ui.preferences.form.row import SettingRow
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.ui.preferences.scope_api import ScopeChoice

USER, LOCAL, BRANCH, BEAMLINE = "user:u1", "local", "branch:7.0.1.1", "beamline:7.0.1"
WRITABLE = (USER, LOCAL, BRANCH)
ORDER = (BEAMLINE, BRANCH, LOCAL, USER)  # broadest first

SITE_KEYS = {"agent_backend", "claude_endpoint", "claude_custom_url", "claude_disable_betas",
             "agy_gcp_project", "agy_gcp_location"}
USER_KEYS = {"claude_model", "claude_effort", "claude_max_turns", "claude_permission_mode",
             "claude_auto_restore", "agy_model", "agy_effort"}
CLAUDE_SECTIONS = ("Claude endpoint", "Claude sign-in")


class FakePrefs(QObject):
    """Layers per scope; a key resolves to its narrowest layer."""

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

    def get(self, key, default=None):
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


class FakeInfo:
    def __init__(self, kind, status, argv=("agy",), gate=None, error=None):
        self.kind = kind
        self.status = status
        self.argv = argv
        self.gate = gate
        self.error = error
        self.refreshes: list[bool] = []

    def auth_status(self, refresh=False):
        self.refreshes.append(refresh)
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return self.status

    def list_models(self, refresh=False):
        return [ModelOption("", "Default (agy)"), ModelOption("gemini-4-argon", "Gemini 4 Argon")]

    def sign_in_argv(self):
        return list(self.argv) if self.argv else None

    def sign_in_env(self):
        return {"USERPROFILE": "C:/agy-home"}


@pytest.fixture
def env(monkeypatch):
    infos = {"claude-code": FakeInfo("claude-code", AuthStatus(True, "Using API key")),
             "agy": FakeInfo("agy", AuthStatus(False, "Not signed in: x"))}
    calls: list[tuple] = []

    def fake_backend_info(kind=None, *, agy_path=None):
        calls.append((kind, agy_path))
        return infos["agy" if kind == "agy" else "claude-code"]

    monkeypatch.setattr(backend_info_mod, "backend_info", fake_backend_info)
    monkeypatch.setattr(assistant_editors, "backend_info", fake_backend_info)
    monkeypatch.setattr(assistant_editors, "get_cached_models", lambda *a, **k: None)
    monkeypatch.setattr(assistant_editors, "cached_model_ids", lambda *a, **k: None)
    monkeypatch.setattr(cs, "check_oauth_status", lambda: (False, "Not logged in"))
    spawned: list[tuple] = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)))
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    pages = [cs.ClaudeSettingsPlugin()]
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
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text, *a, **k: boxes.append(text))
    yield SimpleNamespace(prefs=prefs, pages=pages, boxes=boxes, infos=infos, calls=calls, spawned=spawned)


def _dialog(qtbot, owned=True):
    dialog = PreferencesDialog()
    if owned:  # qtbot closes it at teardown; a test that deletes the dialog itself passes False
        qtbot.addWidget(dialog)
    dialog.prompts = []
    dialog._prompt_restart = lambda: dialog.prompts.append(list(dialog._restart_reasons))
    return dialog


def _form(dialog) -> SettingsForm:
    return next(f for f in dialog.findChildren(SettingsForm) if f._plugin.name == "claude")


def _built(dialog) -> set[str]:
    return {r.key for r in _form(dialog).findChildren(SettingRow)}


def _shown(dialog) -> set[str]:
    form = _form(dialog)
    return {r.key for r in form.findChildren(SettingRow) if r.isVisibleTo(form)}


def _row_of(dialog, key) -> SettingRow:
    return next(r for r in _form(dialog).findChildren(SettingRow) if r.key == key)


def _boxes_shown(dialog) -> set[str]:
    form = _form(dialog)
    return {b.title() for b in form.findChildren(QGroupBox) if b.isVisibleTo(form)}


def _pick(dialog, key, text):
    box = _row_of(dialog, key).editor.findChild(QComboBox)
    index = box.findText(text)
    assert index >= 0, (key, text, [box.itemText(i) for i in range(box.count())])
    box.setCurrentIndex(index)


def _type(dialog, key, text):
    edit = _row_of(dialog, key).editor.findChild(QLineEdit)
    edit.setText(text)
    edit.textEdited.emit(text)


def _action(dialog, label) -> ActionItem:
    return next(a for a in _form(dialog).findChildren(ActionItem) if a.action.label == label)


def _info_texts(dialog) -> list[str]:
    form = _form(dialog)
    return [i.text() for i in form.findChildren(InfoItem) if i.isVisibleTo(form)]


def _wait_idle(qtbot, dialog):
    for item in _form(dialog).findChildren(ActionItem):
        qtbot.waitUntil(lambda item=item: not item.is_running, timeout=3000)


def _frozen(**kw):
    return SimpleNamespace(scope=USER, effective=lambda key, default=None: kw.get(key, default),
                           get=lambda key: kw.get(key), is_dirty=lambda key: False)


# Schema ------------------------------------------------------------------------

def test_specs_scopes_per_constraints(qapp):
    page = cs.ClaudeSettingsPlugin()
    assert (page.name, page.display_name, page.category, page.priority) == ("claude", "Assistant", "plugins", 60)
    specs = {s.key: s for s in page.specs()}
    assert set(specs) == SITE_KEYS | USER_KEYS
    for key in SITE_KEYS:
        assert specs[key].scopes == {"facility", "beamline", "branch", "user"}, key
    for key in USER_KEYS:
        assert specs[key].scopes == {"user"}, key
    assert all(s.label for s in specs.values())
    assert console_only("claude_api_key") and console_only("agy_path")
    titles = [s.title for s in page.sections()]
    assert titles == ["Backend", "Claude endpoint", "Claude sign-in", "Gemini (agy)", "Model and limits", "Behavior"]


def test_validate_values_custom_url():
    page = cs.ClaudeSettingsPlugin()
    message = "Custom URL is required when using custom endpoint"
    assert page.validate_values(_frozen(claude_endpoint="custom")) == [message]
    assert page.validate_values(_frozen(claude_endpoint="custom", claude_custom_url="  ")) == [message]
    assert page.validate_values(_frozen(claude_endpoint="custom", claude_custom_url="https://x.org")) == []
    assert page.validate_values(_frozen(claude_endpoint="cborg")) == []
    assert page.validate_values(_frozen()) == []


# Scopes ------------------------------------------------------------------------

def test_user_scope_hides_api_key_and_agy_path(qtbot, env):
    env.prefs.layers[(LOCAL, "claude_api_key")] = "sk-ant-secret"
    dialog = _dialog(qtbot)
    built = _built(dialog)
    assert "claude_api_key" not in built and "agy_path" not in built
    assert SITE_KEYS - {"agy_gcp_project", "agy_gcp_location"} <= _shown(dialog)
    texts = _info_texts(dialog)
    assert "Using API key from settings" in texts  # the key set at This console is in effect
    assert not any("sk-ant-secret" in t for t in texts)
    _wait_idle(qtbot, dialog)


def test_auth_source_falls_back_to_env_then_oauth(qtbot, env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    dialog = _dialog(qtbot)
    assert "Using env var ANTHROPIC_AUTH_TOKEN" in _info_texts(dialog)
    _wait_idle(qtbot, dialog)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN")
    monkeypatch.setattr(cs, "check_oauth_status", lambda: (True, "Logged in via OAuth (subscription)"))
    dialog2 = _dialog(qtbot)
    texts = _info_texts(dialog2)
    assert "Using OAuth (no API key needed)" in texts
    assert any("Logged in via OAuth (subscription)" in t for t in texts)
    _wait_idle(qtbot, dialog2)


def test_console_scope_shows_api_key_and_writes_local(qtbot, env):
    dialog = _dialog(qtbot)
    dialog.select_scope(LOCAL)
    assert _built(dialog) == {"claude_api_key", "agy_path"}
    assert "claude_api_key" in _shown(dialog)
    edit = _row_of(dialog, "claude_api_key").editor.findChild(QLineEdit)
    assert edit.echoMode() == QLineEdit.EchoMode.Password
    assert edit.placeholderText() == "sk-ant-... (optional if using OAuth)"
    _type(dialog, "claude_api_key", "sk-ant-123")
    assert "Using API key from settings" in _info_texts(dialog)
    assert dialog._apply() is True
    assert env.prefs.calls == [("claude_api_key", "sk-ant-123", LOCAL)]
    _wait_idle(qtbot, dialog)


def test_beamline_scope_shows_only_site_keys(qtbot, env):
    dialog = _dialog(qtbot)
    dialog.select_scope(BEAMLINE)
    assert _built(dialog) == SITE_KEYS
    dialog.select_scope(BRANCH)
    assert _built(dialog) == SITE_KEYS
    dialog.select_scope(USER)
    assert _built(dialog) == SITE_KEYS | {"claude_model", "claude_max_turns", "claude_effort",
                                          "claude_permission_mode", "claude_auto_restore", "agy_model"}
    _wait_idle(qtbot, dialog)


# Backend switch ----------------------------------------------------------------

def test_switching_backend_swaps_sections_before_save(qtbot, env):
    dialog = _dialog(qtbot)
    boxes = _boxes_shown(dialog)
    assert set(CLAUDE_SECTIONS) <= boxes and "Gemini (agy)" not in boxes
    assert "claude_model" in _shown(dialog) and "agy_model" not in _shown(dialog)
    _wait_idle(qtbot, dialog)
    _pick(dialog, "agent_backend", "Gemini (agy)")
    boxes = _boxes_shown(dialog)
    assert "Gemini (agy)" in boxes and not set(CLAUDE_SECTIONS) & boxes
    shown = _shown(dialog)
    assert {"agy_gcp_project", "agy_gcp_location", "agy_model", "claude_max_turns"} <= shown
    assert "claude_model" not in shown and "claude_effort" not in shown
    assert _action(dialog, "Sign in…").isVisibleTo(_form(dialog))
    assert not _action(dialog, "Log in with browser").isVisibleTo(_form(dialog))
    # the status check re-runs for the newly chosen backend
    qtbot.waitUntil(lambda: _action(dialog, "Check status").status.text() == "Not signed in: x", timeout=3000)
    assert dialog._apply() is True
    assert env.prefs.calls == [("agent_backend", "agy", USER)]
    _wait_idle(qtbot, dialog)


def test_untouched_page_writes_nothing(qtbot, env):
    env.prefs.layers[(USER, "claude_max_turns")] = 40
    env.prefs.layers[(BRANCH, "claude_endpoint")] = "cborg"
    env.prefs.layers[(LOCAL, "claude_api_key")] = "sk-ant-x"
    env.prefs.layers[(USER, "agy_model")] = "gemini-4-argon-medium"
    dialog = _dialog(qtbot)
    _wait_idle(qtbot, dialog)
    dialog.select_scope(LOCAL)
    dialog.select_scope(BRANCH)
    _wait_idle(qtbot, dialog)
    assert dialog._apply() is True
    assert env.prefs.calls == []


def test_user_edits_write_the_old_values(qtbot, env):
    """The old page wrote these (unscoped, every save); the form writes the edited ones at User."""
    dialog = _dialog(qtbot)
    _pick(dialog, "agent_backend", "Claude Code")
    _pick(dialog, "claude_endpoint", "Anthropic API")
    _pick(dialog, "claude_disable_betas", "Off")
    _type(dialog, "claude_max_turns", "20")
    _pick(dialog, "claude_permission_mode", "default - Requires confirmation for actions")
    _pick(dialog, "claude_auto_restore", "Off")
    _pick(dialog, "claude_effort", "high")
    assert dialog._apply() is True
    assert sorted(env.prefs.calls, key=str) == sorted([
        ("agent_backend", "claude-code", USER),
        ("claude_endpoint", "anthropic", USER),
        ("claude_disable_betas", False, USER),
        ("claude_max_turns", 20, USER),
        ("claude_permission_mode", "default", USER),
        ("claude_auto_restore", False, USER),
        ("claude_effort", "high", USER),
    ], key=str)
    assert type(env.prefs.layers[(USER, "claude_max_turns")]) is int
    _wait_idle(qtbot, dialog)


def test_custom_endpoint_requires_url(qtbot, env):
    dialog = _dialog(qtbot)
    url = _row_of(dialog, "claude_custom_url").editor
    assert not url.isEnabled()
    assert url.findChild(QLineEdit).placeholderText() == "https://your-api-server.com"
    _pick(dialog, "claude_endpoint", "Custom...")
    assert url.isEnabled()
    assert dialog._apply() is False
    assert any("Custom URL is required when using custom endpoint" in b for b in env.boxes)
    assert env.prefs.calls == []
    _type(dialog, "claude_custom_url", "https://api.example.org")
    assert dialog._apply() is True
    assert sorted(env.prefs.calls, key=str) == sorted(
        [("claude_endpoint", "custom", USER), ("claude_custom_url", "https://api.example.org", USER)], key=str)
    _wait_idle(qtbot, dialog)


# Status check ------------------------------------------------------------------

def test_status_action_runs_on_show_and_failure_text(qtbot, env):
    dialog = _dialog(qtbot, owned=False)
    status = _action(dialog, "Check status")
    qtbot.waitUntil(lambda: status.status.text() == "✓ Using API key", timeout=3000)
    assert env.infos["claude-code"].refreshes == [True]

    env.infos["claude-code"].error = RuntimeError("boom")
    status.button.click()
    qtbot.waitUntil(lambda: status.status.text() == "Couldn't check: boom", timeout=3000)

    # A slow check: the dialog stays usable, and closing it first raises nothing.
    gate = threading.Event()
    env.infos["claude-code"].error = None
    env.infos["claude-code"].gate = gate
    status.button.click()
    future = status.future
    assert status.is_running and status.status.text() == "Working…"
    _pick(dialog, "claude_endpoint", "LBNL Cloud (cborg)")  # the GUI thread is free
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(status)
    gate.set()
    assert future.wait(5000)
    QApplication.processEvents()
    QCoreApplication.sendPostedEvents()


def test_check_status_unit(env):
    env.infos["agy"].status = AuthStatus(True, "Signed in (3 models)")
    assert cs._check_status(_frozen(agent_backend="agy", agy_path="D:/agy.exe")) == "✓ Signed in (3 models)"
    assert env.calls[-1] == ("agy", "D:/agy.exe")
    env.infos["claude-code"].status = AuthStatus(False, "No authentication")
    assert cs._check_status(_frozen()) == "No authentication"
    assert env.calls[-1] == ("claude-code", None)


# Sign-in actions ---------------------------------------------------------------

def test_claude_login_spawns_cli(env, monkeypatch):
    assert cs._claude_login(_frozen()) == "Browser opened — complete login there"
    assert env.spawned[0][0] == ["claude", "login"]

    def missing(*_a, **_k):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "Popen", missing)
    assert cs._claude_login(_frozen()) == "Claude CLI not found. Install with: npm i -g @anthropic-ai/claude-code"


def test_agy_sign_in_spawns_console_and_polls(env, monkeypatch):
    monkeypatch.setattr(cs, "AGY_SIGN_IN_POLL_S", 0.01)
    monkeypatch.setattr(cs, "AGY_SIGN_IN_TIMEOUT_S", 5.0)
    agy = env.infos["agy"]
    statuses = iter([AuthStatus(False, "no"), AuthStatus(True, "Signed in (2 models)")])
    agy.auth_status = lambda refresh=False: (agy.refreshes.append(refresh), next(statuses))[1]
    assert cs._agy_sign_in(_frozen(agy_path="D:/agy.exe")) == "✓ Signed in (2 models)"
    argv, kwargs = env.spawned[0]
    assert argv == ["agy"]
    assert kwargs["env"]["USERPROFILE"] == "C:/agy-home"
    if sys.platform == "win32":
        assert kwargs["creationflags"] == subprocess.CREATE_NEW_CONSOLE
    assert agy.refreshes == [True, True]
    assert env.calls[-1] == ("agy", "D:/agy.exe")


def test_agy_sign_in_times_out(env, monkeypatch):
    monkeypatch.setattr(cs, "AGY_SIGN_IN_POLL_S", 0.01)
    monkeypatch.setattr(cs, "AGY_SIGN_IN_TIMEOUT_S", 0.05)
    assert cs._agy_sign_in(_frozen()) == "Not signed in — try again"
    assert env.infos["agy"].refreshes  # it polled


def test_agy_sign_in_without_agy(env):
    env.infos["agy"].argv = None
    assert cs._agy_sign_in(_frozen()) == "agy (Antigravity CLI) not found — install it or set its path"
    assert env.spawned == []


# test_connection ---------------------------------------------------------------

class _Client:
    def __init__(self, outcome, seen):
        self._outcome = outcome
        self._seen = seen

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        self._seen.append((url, json, headers))
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return SimpleNamespace(status_code=self._outcome)


def test_test_connection_texts(monkeypatch):
    seen: list = []
    kwargs_seen: list = []

    def use(outcome):
        def factory(**kwargs):
            kwargs_seen.append(kwargs)
            return _Client(outcome, seen)
        monkeypatch.setattr(httpx, "Client", factory)

    from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider

    monkeypatch.setattr(ProxySettingsProvider, "should_use_proxy_for_url",
                        staticmethod(lambda url: "socks5://localhost:1080" if "lbl.gov" in url else None))
    url = "https://api.anthropic.com"
    assert cs.test_connection(url, None, "m") == "No API key configured"
    assert cs.test_connection(url, "", "m") == "No API key configured"
    for code, text in ((200, "Connected!"), (401, "Invalid API key"), (403, "Access denied"),
                       (500, "Error: HTTP 500")):
        use(code)
        assert cs.test_connection(url, "sk-1", "claude-x") == text
    assert seen[-1][0] == "https://api.anthropic.com/v1/messages"
    assert seen[-1][1]["model"] == "claude-x"
    assert seen[-1][2]["x-api-key"] == "sk-1"
    assert "proxy" not in kwargs_seen[-1]
    use(httpx.TimeoutException("slow"))
    assert cs.test_connection(url, "sk-1", "m") == "Connection timeout"
    use(httpx.ConnectError("refused"))
    assert cs.test_connection(url, "sk-1", "m") == "Could not connect to server"
    use(200)
    cs.test_connection("https://api.cborg.lbl.gov", "sk-1", "m")
    assert kwargs_seen[-1]["proxy"] == "socks5://localhost:1080"
    use(ValueError("bad header sk-secret-9"))
    text = cs.test_connection(url, "sk-secret-9", "m")
    assert text.startswith("Error: ") and "sk-secret-9" not in text


def test_test_connection_action_uses_effective_values(env, monkeypatch):
    got = []
    monkeypatch.setattr(cs, "test_connection", lambda *a: (got.append(a), "Connected!")[1])
    assert cs._test_connection_action(_frozen(claude_endpoint="cborg", claude_api_key="k",
                                              claude_model="claude-opus")) == "Connected!"
    assert got[-1] == ("https://api.cborg.lbl.gov", "k", "claude-opus")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "envkey")
    cs._test_connection_action(_frozen(claude_endpoint="custom", claude_custom_url="https://x.org/"))
    assert got[-1][:2] == ("https://x.org", "envkey")


@pytest.mark.parametrize("stored", [None, "proj-1"])
def test_cleared_text_field_is_not_set_never_empty(qtbot, env, stored):
    env.prefs.layers[(USER, "agent_backend")] = "agy"
    if stored is not None:
        env.prefs.layers[(USER, "agy_gcp_project")] = stored
    dialog = _dialog(qtbot)
    _type(dialog, "agy_gcp_project", "typed-project")
    _type(dialog, "agy_gcp_project", "  ")
    _type(dialog, "agy_gcp_location", "")
    assert dialog._apply() is True
    assert all(value != "" for _key, value, _scope in env.prefs.calls)
    expected = [] if stored is None else [("agy_gcp_project", None, USER)]  # a stored value is removed
    assert env.prefs.calls == expected
    _wait_idle(qtbot, dialog)
