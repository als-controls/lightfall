"""Assistant settings: backend selector, Gemini group, async status, Sign in…"""
from __future__ import annotations

import pytest
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


def _signed_out_infos():
    agy = _Info("agy", AuthStatus(False, "Not signed in: x"))
    return agy, {"agy": agy, "claude-code": _Info("claude-code", AuthStatus(True, "k"))}


def test_dialog_finish_stops_sign_in_polling(qtbot, prefs):
    from PySide6.QtWidgets import QDialog

    prefs.values["agent_backend"] = "agy"
    agy, infos = _signed_out_infos()
    plugin = cs.ClaudeSettingsPlugin()
    plugin._backend = AgentBackendSection(info_factory=lambda kind, agy_path=None: infos[kind],
                                          spawn=lambda *a, **k: None, poll_interval_ms=30,
                                          poll_timeout_s=30)
    plugin._backend.on_kind_changed = plugin._apply_backend_visibility
    dialog = QDialog()
    qtbot.addWidget(dialog)
    w = plugin.create_widget(dialog)
    plugin.load_settings()
    qtbot.waitUntil(lambda: "Not signed in" in _status(w), timeout=3000)
    w.findChild(QPushButton, "agySignIn").click()
    assert plugin._backend._poll_timer.isActive()
    qtbot.waitUntil(lambda: len(agy.refreshes) >= 2, timeout=3000)  # polling runs
    dialog.done(0)  # Preferences closed; the (hidden) page itself stays alive
    assert not plugin._backend._poll_timer.isActive()
    qtbot.wait(100)  # let any in-flight check land
    n = len(agy.refreshes)
    qtbot.wait(200)
    assert len(agy.refreshes) == n


def test_new_page_stops_previous_poll(qtbot, prefs):
    prefs.values["agent_backend"] = "agy"
    _agy, infos = _signed_out_infos()
    sec, host = _section(qtbot, infos, poll_interval_ms=30, poll_timeout_s=30)
    sec.load()
    host.findChild(QPushButton, "agySignIn").click()
    old_timer = sec._poll_timer
    assert old_timer.isActive()
    group = sec.create_backend_group()  # a new Preferences page replaces the old one
    qtbot.addWidget(group)
    assert not old_timer.isActive()


def test_late_result_after_stop_is_ignored(qtbot, prefs):
    import threading

    gate = threading.Event()
    prefs.values["agent_backend"] = "agy"
    infos = {"agy": _Info("agy", AuthStatus(True, "Signed in (1 models)"), gate=gate),
             "claude-code": _Info("claude-code", AuthStatus(True, "Using API key"))}
    sec, host = _section(qtbot, infos)
    sec.load()  # check in flight, gated
    sec.stop()
    host.findChild(QLabel, "assistantBackendStatus").setText("sentinel")
    gate.set()
    qtbot.wait(300)
    assert _status(host) == "sentinel"


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


def test_slow_check_is_not_superseded_by_polling(qtbot, prefs):
    import threading

    gate = threading.Event()
    prefs.values["agent_backend"] = "agy"
    spawned = []
    agy = _Info("agy", AuthStatus(True, "Signed in (2 models)"), gate=gate)
    infos = {"agy": agy, "claude-code": _Info("claude-code", AuthStatus(True, "k"))}
    sec, host = _section(qtbot, infos, spawn=lambda argv, **kw: spawned.append(argv),
                         poll_interval_ms=30, poll_timeout_s=30)
    sec.load()  # slow initial check, gated
    qtbot.wait(100)
    host.findChild(QPushButton, "agySignIn").click()  # starts polling
    qtbot.wait(300)  # many poll intervals pass while the check is gated
    assert len(agy.refreshes) == 1
    gate.set()
    qtbot.waitUntil(lambda: "Signed in" in _status(host), timeout=3000)
    assert not sec._poll_timer.isActive()


def test_save_without_widgets_leaves_prefs_alone(prefs):
    prefs.values["agent_backend"] = "agy"
    AgentBackendSection(info_factory=lambda *a, **k: None).save()
    assert prefs.values == {"agent_backend": "agy"}


def test_sign_in_message_not_overwritten_by_stale_check(qtbot, prefs):
    import threading

    gate = threading.Event()
    prefs.values["agent_backend"] = "agy"
    agy = _Info("agy", AuthStatus(False, "Not signed in: stale"), gate=gate)
    infos = {"agy": agy, "claude-code": _Info("claude-code", AuthStatus(True, "k"))}
    sec, host = _section(qtbot, infos, poll_interval_ms=100000)
    sec.load()  # initial check in flight
    host.findChild(QPushButton, "agySignIn").click()
    gate.set()
    qtbot.wait(300)
    assert "Complete the Google sign-in" in _status(host)
