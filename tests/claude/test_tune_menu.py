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
