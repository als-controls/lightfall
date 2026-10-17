"""Tune menu lists the active backend's models from the non-blocking cache."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMenu, QWidget

from lightfall.agents.backends import info as info_mod
from lightfall.agents.backends.info import BackendCaps, ModelOption
from lightfall.claude.widget import ClaudeAssistantWidget


class _Info:
    caps = BackendCaps(cost=False, live_set_model=False, subagents=False)

    def cached_models(self):
        return [ModelOption("", "Default (agy)"), ModelOption("gemini-3-pro", "Gemini 3 Pro")]

    def current_model(self):
        return "gemini-3-pro"

    def list_models(self, refresh=False):
        raise AssertionError("must not block the GUI thread")


@pytest.fixture
def widget(qtbot, monkeypatch):
    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: _Info())
    w = ClaudeAssistantWidget.__new__(ClaudeAssistantWidget)
    QWidget.__init__(w)
    qtbot.addWidget(w)
    w._tune_menu = QMenu(w)
    w.bus_endpoint = SimpleNamespace(policy="queue")
    return w


def test_menu_lists_backend_models_with_next_turn_header(widget):
    widget._build_tune_menu()
    texts = [a.text() for a in widget._tune_menu.actions()]
    assert "Model (next turn)" in texts
    assert "Gemini 3 Pro" in texts and "Default (agy)" in texts
    checked = [a.text() for a in widget._tune_menu.actions() if a.isChecked()]
    assert "Gemini 3 Pro" in checked


def test_picking_emits_model_id(widget, qtbot):
    widget._build_tune_menu()
    act = next(a for a in widget._tune_menu.actions() if a.text() == "Gemini 3 Pro")
    with qtbot.waitSignal(widget.model_change_requested) as sig:
        act.trigger()
    assert sig.args == ["gemini-3-pro"]


class _ColdInfo(_Info):
    """Cold cache: only the default entry until list_models() fills it."""

    def __init__(self, current="gemini-3-pro", gate=None):
        self._current, self._gate = current, gate
        self.list_calls = []

    def cached_models(self):
        return [ModelOption("", "Default (agy)")]

    def current_model(self):
        return self._current

    def list_models(self, refresh=False):
        self.list_calls.append(refresh)
        if self._gate is not None:
            self._gate.wait(3)
        return self.cached_models()


def test_current_model_missing_from_cache_is_listed_and_checked(widget, monkeypatch):
    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: _ColdInfo("gemini-9-ultra"))
    widget._build_tune_menu()
    checked = [a.text() for a in widget._tune_menu.actions() if a.isChecked()]
    assert "gemini-9-ultra" in checked


def test_custom_claude_model_not_in_presets_is_listed(widget, monkeypatch):
    class _Claude(_Info):
        def cached_models(self):
            return [ModelOption("", "Default (CLI)"), ModelOption("claude-opus", "claude-opus")]

        def current_model(self):
            return "claude-opus-4-8"

    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: _Claude())
    widget._build_tune_menu()
    checked = [a.text() for a in widget._tune_menu.actions() if a.isChecked()]
    assert "claude-opus-4-8" in checked and "claude-opus" not in checked


def test_cold_cache_warms_once_in_background(widget, monkeypatch, qtbot):
    import threading

    gate = threading.Event()
    cold = _ColdInfo(gate=gate)
    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: cold)
    widget._build_tune_menu()
    widget._build_tune_menu()  # second open while the warm-up is still running
    qtbot.waitUntil(lambda: len(cold.list_calls) == 1, timeout=3000)
    gate.set()
    qtbot.waitUntil(lambda: not widget._models_future.isRunning(), timeout=3000)
    assert cold.list_calls == [False]  # one warm-up, using the TTL cache


def test_warm_cache_starts_no_background_list(widget, monkeypatch, qtbot):
    calls = []

    class _Warm(_Info):
        def list_models(self, refresh=False):
            calls.append(refresh)
            return []

    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: _Warm())
    widget._build_tune_menu()
    qtbot.wait(200)
    assert calls == []
    assert getattr(widget, "_models_future", None) is None
