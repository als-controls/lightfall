"""Settings → Assistant: the Claude model combo never fetches on the GUI thread."""
from __future__ import annotations

import threading

import pytest

from lightfall.agents.backends.info import AuthStatus
from lightfall.ui.preferences import claude_settings as cs
from lightfall.ui.preferences.agent_backend_section import AgentBackendSection


class _FakePrefs:
    def __init__(self, data=None):
        self.values = dict(data or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class _Info:
    def __init__(self, kind):
        self.kind = kind

    def auth_status(self, refresh=False):
        return AuthStatus(True, "ok")


class _Fetch:
    """Stands in for get_cached_models; each call can be held by a gate."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []  # (base_url, thread ident)
        self.gates = []

    def __call__(self, base_url, api_key, *, refresh=False):
        result = self.results.pop(0) if self.results else None  # in call order
        gate = threading.Event()
        self.gates.append(gate)
        self.calls.append((base_url, threading.get_ident()))
        gate.wait(3)
        return result


@pytest.fixture
def prefs(monkeypatch):
    store = _FakePrefs({"claude_api_key": "k", "claude_endpoint": "anthropic",
                        "claude_model": "claude-sonnet"})
    monkeypatch.setattr(cs.PreferencesManager, "get_instance", classmethod(lambda cls: store))
    monkeypatch.setattr(cs, "_MODELS_CACHE", {})
    monkeypatch.setattr(cs, "check_oauth_status", lambda: (False, "no"))
    return store


def _page(qtbot, monkeypatch, fetch):
    monkeypatch.setattr(cs, "get_cached_models", fetch)
    plugin = cs.ClaudeSettingsPlugin()
    plugin._backend = AgentBackendSection(info_factory=lambda kind, agy_path=None: _Info(kind))
    plugin._backend.on_kind_changed = plugin._apply_backend_visibility
    w = plugin.create_widget()
    qtbot.addWidget(w)
    return plugin


def _items(plugin):
    combo = plugin._model_combo
    return [combo.itemText(i) for i in range(combo.count())]


def test_load_returns_before_the_fetch_and_fills_later(qtbot, monkeypatch, prefs):
    fetch = _Fetch(["claude-x-1", "claude-sonnet"])
    plugin = _page(qtbot, monkeypatch, fetch)
    plugin.load_settings()  # must not wait for the gated fetch
    assert _items(plugin) == ["", "claude-opus", "claude-sonnet", "claude-haiku"]
    assert plugin._model_combo.currentText() == "claude-sonnet"
    qtbot.waitUntil(lambda: len(fetch.gates) == 1, timeout=3000)
    assert fetch.calls[0] == ("https://api.anthropic.com", fetch.calls[0][1])
    assert fetch.calls[0][1] != threading.get_ident()
    fetch.gates[0].set()
    qtbot.waitUntil(lambda: _items(plugin) == ["", "claude-x-1", "claude-sonnet"], timeout=3000)
    assert plugin._model_combo.currentText() == "claude-sonnet"


def test_typed_model_survives_the_refill(qtbot, monkeypatch, prefs):
    fetch = _Fetch(["claude-x-1"])
    plugin = _page(qtbot, monkeypatch, fetch)
    plugin.load_settings()
    plugin._model_combo.setEditText("my-custom-model")
    qtbot.waitUntil(lambda: len(fetch.gates) == 1, timeout=3000)
    fetch.gates[0].set()
    qtbot.waitUntil(lambda: "claude-x-1" in _items(plugin), timeout=3000)
    assert plugin._model_combo.currentText() == "my-custom-model"


def test_cached_models_fill_without_a_fetch(qtbot, monkeypatch, prefs):
    cs._MODELS_CACHE["https://api.anthropic.com"] = ["claude-cached"]
    fetch = _Fetch()
    plugin = _page(qtbot, monkeypatch, fetch)
    plugin.load_settings()
    assert _items(plugin) == ["", "claude-cached"]
    qtbot.wait(100)
    assert fetch.calls == []


def test_reload_while_fetching_does_not_stack(qtbot, monkeypatch, prefs):
    fetch = _Fetch(["claude-x-1"])
    plugin = _page(qtbot, monkeypatch, fetch)
    plugin.load_settings()
    qtbot.waitUntil(lambda: len(fetch.gates) == 1, timeout=3000)
    plugin.load_settings()  # the dialog reopened while the first fetch runs
    qtbot.wait(100)
    assert len(fetch.calls) == 1
    fetch.gates[0].set()
    qtbot.waitUntil(lambda: "claude-x-1" in _items(plugin), timeout=3000)


def test_result_for_a_superseded_endpoint_is_dropped(qtbot, monkeypatch, prefs):
    fetch = _Fetch(["stale-model"], ["fresh-model"])
    plugin = _page(qtbot, monkeypatch, fetch)
    plugin.load_settings()
    qtbot.waitUntil(lambda: len(fetch.gates) == 1, timeout=3000)
    prefs.values["claude_endpoint"] = "cborg"
    plugin.load_settings()  # different base URL: a new fetch supersedes the old one
    qtbot.waitUntil(lambda: len(fetch.gates) == 2, timeout=3000)
    assert fetch.calls[1][0] == "https://api.cborg.lbl.gov"
    fetch.gates[1].set()
    qtbot.waitUntil(lambda: "fresh-model" in _items(plugin), timeout=3000)
    fetch.gates[0].set()
    qtbot.wait(200)
    assert "stale-model" not in _items(plugin)


def test_result_after_the_page_is_gone_is_ignored(qtbot, monkeypatch, prefs):
    fetch = _Fetch(["claude-x-1"])
    plugin = _page(qtbot, monkeypatch, fetch)
    plugin.load_settings()
    qtbot.waitUntil(lambda: len(fetch.gates) == 1, timeout=3000)
    combo = plugin._model_combo
    combo.setParent(None)
    combo.deleteLater()
    qtbot.waitUntil(lambda: not _alive(combo), timeout=3000)
    fetch.gates[0].set()
    qtbot.wait(200)  # no RuntimeError from a dead combo


def _alive(obj):
    import shiboken6

    return shiboken6.isValid(obj)
