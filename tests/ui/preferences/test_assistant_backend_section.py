"""Assistant settings: backend selector, Gemini group, async status, Sign in…"""
from __future__ import annotations

import pytest
import shiboken6
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from lightfall.agents.backends.info import AuthStatus, ModelOption
from lightfall.ui.preferences import claude_settings as cs
from lightfall.ui.preferences.agent_backend_section import AgentBackendSection


class _FakePrefs:
    def __init__(self, data=None):
        self.values = dict(data or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture
def prefs(monkeypatch):
    store = _FakePrefs()
    monkeypatch.setattr(cs.PreferencesManager, "get_instance", classmethod(lambda cls: store))
    monkeypatch.setattr(cs, "get_cached_models", lambda *a, **k: None)  # no network
    return store


class _Info:
    def __init__(self, kind, status, models=(), argv=("agy",), gate=None):
        self.kind, self._status, self._models, self._argv, self._gate = kind, status, models, argv, gate
        self.refreshes = []

    def auth_status(self, refresh=False):
        self.refreshes.append(refresh)
        if self._gate is not None:
            self._gate.wait(3)
        return self._status

    def list_models(self, refresh=False):
        return [ModelOption("", "Default (agy)"), *self._models]

    def sign_in_argv(self):
        return list(self._argv) if self._argv else None

    def sign_in_env(self):
        return {"USERPROFILE": "C:/agy-home", "HOME": "C:/agy-home"}


def _section(qtbot, infos, spawn=None, **kw):
    sec = AgentBackendSection(info_factory=lambda kind, agy_path=None: infos[kind],
                              spawn=spawn or (lambda *a, **k: None), **kw)
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.addWidget(sec.create_backend_group())
    lay.addWidget(sec.create_gemini_group())
    qtbot.addWidget(host)
    return sec, host


def _status(host):
    return host.findChild(QLabel, "assistantBackendStatus").text()


def test_load_save_roundtrip(qtbot, prefs):
    prefs.values.update(agent_backend="agy", agy_path="D:/agy.exe", agy_gcp_project="p1",
                        agy_gcp_location="us", agy_model="gemini-3-pro")
    infos = {"agy": _Info("agy", AuthStatus(True, "Signed in (1 models)"),
                          [ModelOption("gemini-3-pro", "Gemini 3 Pro")]),
             "claude-code": _Info("claude-code", AuthStatus(True, "Using API key"))}
    sec, host = _section(qtbot, infos)
    sec.load()
    assert sec.selected_kind() == "agy"
    host.findChild(QLineEdit, "agyProject").setText("p2")
    sec.save()
    assert prefs.values["agent_backend"] == "agy"
    assert prefs.values["agy_gcp_project"] == "p2"
    assert prefs.values["agy_path"] == "D:/agy.exe"
    assert prefs.values["agy_model"] == "gemini-3-pro"


def test_status_fills_async_and_models_populate(qtbot, prefs):
    prefs.values["agent_backend"] = "agy"
    infos = {"agy": _Info("agy", AuthStatus(True, "Signed in (1 models)"),
                          [ModelOption("gemini-3-pro", "Gemini 3 Pro")]),
             "claude-code": _Info("claude-code", AuthStatus(True, "Using API key"))}
    sec, host = _section(qtbot, infos)
    sec.load()
    qtbot.waitUntil(lambda: "Signed in" in _status(host), timeout=3000)
    combo = host.findChild(QComboBox, "agyModel")
    assert [combo.itemData(i) for i in range(combo.count())] == ["", "gemini-3-pro"]


def test_kind_change_toggles_gemini_group(qtbot, prefs):
    infos = {"agy": _Info("agy", AuthStatus(False, "Not signed in: x")),
             "claude-code": _Info("claude-code", AuthStatus(True, "Using API key"))}
    sec, host = _section(qtbot, infos)
    seen = []
    sec.on_kind_changed = seen.append
    sec.load()
    gemini = host.findChild(QWidget, "assistantGeminiGroup")
    assert gemini.isHidden()
    kind_combo = host.findChild(QComboBox, "assistantBackend")
    kind_combo.setCurrentIndex(kind_combo.findData("agy"))
    assert not gemini.isHidden() and seen[-1] == "agy"


def test_stale_result_is_dropped(qtbot, prefs):
    import threading

    gate = threading.Event()
    infos = {"agy": _Info("agy", AuthStatus(True, "Signed in (9 models)"), gate=gate),
             "claude-code": _Info("claude-code", AuthStatus(True, "Using API key"))}
    sec, host = _section(qtbot, infos)
    sec.load()  # claude-code
    kind_combo = host.findChild(QComboBox, "assistantBackend")
    kind_combo.setCurrentIndex(kind_combo.findData("agy"))      # slow check starts
    kind_combo.setCurrentIndex(kind_combo.findData("claude-code"))
    qtbot.waitUntil(lambda: "Using API key" in _status(host), timeout=3000)
    gate.set()
    qtbot.wait(300)
    assert "Using API key" in _status(host)


def test_result_after_page_destroyed_is_ignored(qtbot, prefs):
    import threading

    gate = threading.Event()
    prefs.values["agent_backend"] = "agy"
    infos = {"agy": _Info("agy", AuthStatus(True, "Signed in (1 models)"), gate=gate),
             "claude-code": _Info("claude-code", AuthStatus(True, "Using API key"))}
    sec, host = _section(qtbot, infos)
    sec.load()
    label = host.findChild(QLabel, "assistantBackendStatus")
    shiboken6.delete(label)  # the page's widgets are gone (Preferences closed)
    gate.set()
    qtbot.wait(300)  # must not raise "Internal C++ object already deleted"


def test_sign_in_spawns_agy_in_private_home_and_polls(qtbot, prefs, monkeypatch):
    prefs.values["agent_backend"] = "agy"
    spawned = []
    agy = _Info("agy", AuthStatus(False, "Not signed in: x"))
    infos = {"agy": agy, "claude-code": _Info("claude-code", AuthStatus(True, "k"))}
    sec, host = _section(qtbot, infos, spawn=lambda argv, **kw: spawned.append((argv, kw)),
                         poll_interval_ms=50, poll_timeout_s=5)
    sec.load()
    qtbot.waitUntil(lambda: "Not signed in" in _status(host), timeout=3000)  # initial check done
    host.findChild(QPushButton, "agySignIn").click()
    assert spawned and spawned[0][0] == ["agy"]
    assert spawned[0][1]["env"]["USERPROFILE"] == "C:/agy-home"
    agy._status = AuthStatus(True, "Signed in (2 models)")
    qtbot.waitUntil(lambda: "Signed in" in _status(host), timeout=3000)
    n = len(agy.refreshes)
    qtbot.wait(200)
    assert len(agy.refreshes) == n  # polling stopped once signed in
    assert True in agy.refreshes   # polls bypass the TTL cache


def test_sign_in_without_agy_shows_message(qtbot, prefs):
    prefs.values["agent_backend"] = "agy"
    infos = {"agy": _Info("agy", AuthStatus(False, "agy (Antigravity CLI) not found"), argv=None),
             "claude-code": _Info("claude-code", AuthStatus(True, "k"))}
    sec, host = _section(qtbot, infos)
    sec.load()
    host.findChild(QPushButton, "agySignIn").click()
    assert "not found" in _status(host)


def test_plugin_page_title_and_hides_claude_groups_on_agy(qtbot, prefs, monkeypatch):
    from lightfall.ui.preferences import agent_backend_section as abs_mod

    prefs.values["agent_backend"] = "agy"
    monkeypatch.setattr(abs_mod, "backend_info",  # never run the real agy here
                        lambda kind=None, agy_path=None: _Info(kind, AuthStatus(False, "x")))
    plugin = cs.ClaudeSettingsPlugin()
    assert plugin.display_name == "Assistant"
    w = plugin.create_widget()
    qtbot.addWidget(w)
    plugin.load_settings()
    api = w.findChild(QWidget, "assistantClaudeApiGroup")
    assert api.isHidden()
