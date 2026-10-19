"""Each Assistant widget keeps the backend descriptor it was built with.

A backend switch saved while a reply runs leaves the old agent live until the
deferred reload rebuilds the widget. Until then the tune menu, model refresh,
speaker name and question header must describe the live agent, not the newly
selected backend.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QLabel, QWidget

from lightfall.agents.backends import info as info_mod
from lightfall.agents.backends.info import AuthStatus, BackendCaps, ModelOption
from lightfall.claude import widget as widget_mod
from lightfall.claude.widgets.question_request import QuestionRequestWidget


class _Desc:
    def __init__(self, kind, name, caps, models):
        self.kind = kind
        self.display_name = name
        self.caps = caps
        self._models = models
        self.list_calls = []

    def cached_models(self):
        return list(self._models)

    def list_models(self, refresh=False):
        self.list_calls.append(refresh)
        return list(self._models)

    def auth_status(self, refresh=False):
        return AuthStatus(True, "signed in")

    def current_model(self):
        return ""

    def effort_options(self, model_id):
        return [""]

    def current_effort(self):
        return ""


def _claude():
    return _Desc("claude-code", "Claude", BackendCaps(cost=True, live_set_model=True, subagents=True),
                 [ModelOption("", "Default (CLI)"), ModelOption("claude-opus", "claude-opus")])


def _gemini():
    return _Desc("agy", "Gemini", BackendCaps(cost=False, live_set_model=False, subagents=True,
                                       live_set_effort=True),
                 [ModelOption("", "Default (agy)"), ModelOption("gemini-3-pro", "Gemini 3 Pro")])


def _build(qtbot, monkeypatch, desc):
    monkeypatch.setattr(widget_mod, "QtClaudeAgent", MagicMock())
    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: desc)
    target = QWidget()
    qtbot.addWidget(target)
    spec = SimpleNamespace(name="lightfall", description="d", on_message="queue")
    w = widget_mod.ClaudeAssistantWidget(target_window=target, spec=spec)
    qtbot.addWidget(w)
    return w


def test_widget_keeps_its_descriptor_after_a_backend_switch(qtbot, monkeypatch):
    claude = _claude()
    w = _build(qtbot, monkeypatch, claude)
    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: _gemini())  # pref switched
    assert w.backend_info is claude
    assert w.speaker_name == "Claude"
    w._build_tune_menu()
    texts = [a.text() for a in w._tune_menu.actions()]
    assert "Model (live)" in texts and "claude-opus" in texts
    assert "Gemini 3 Pro" not in texts


def test_refresh_models_uses_the_widget_descriptor(qtbot, monkeypatch):
    claude = _claude()
    w = _build(qtbot, monkeypatch, claude)
    gemini = _gemini()
    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: gemini)
    w._refresh_models()
    qtbot.waitUntil(lambda: claude.list_calls == [True], timeout=3000)
    assert gemini.list_calls == []


def test_descriptor_lookup_failure_falls_back_to_claude(qtbot, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bad prefs")

    monkeypatch.setattr(widget_mod, "QtClaudeAgent", MagicMock())
    monkeypatch.setattr(info_mod, "backend_info", boom)
    target = QWidget()
    qtbot.addWidget(target)
    spec = SimpleNamespace(name="lightfall", description="d", on_message="queue")
    w = widget_mod.ClaudeAssistantWidget(target_window=target, spec=spec)
    qtbot.addWidget(w)
    assert isinstance(w.backend_info, info_mod.ClaudeCodeInfo)
    assert w.speaker_name == "Claude"


def test_question_header_uses_the_widget_speaker(qtbot, monkeypatch):
    w = _build(qtbot, monkeypatch, _gemini())
    monkeypatch.setattr(info_mod, "backend_info",
                        lambda *a, **k: pytest.fail("question header looked up the pref"))
    w._on_question_requested("rid", [{"question": "Pick?", "options": [{"label": "a"}]}])
    q = w._pending_question_widgets["rid"]
    headers = [lb.text() for lb in q.findChildren(QLabel) if "asking" in lb.text()]
    assert headers and "Gemini is asking" in headers[0]


def test_question_widget_default_speaker_needs_no_lookup(qtbot, monkeypatch):
    monkeypatch.setattr(info_mod, "backend_info",
                        lambda *a, **k: pytest.fail("no backend lookup"))
    q = QuestionRequestWidget("rid", [{"question": "Pick?", "options": [{"label": "a"}]}],
                              speaker="Gemini")
    qtbot.addWidget(q)
    assert any("Gemini is asking" in lb.text() for lb in q.findChildren(QLabel))


def test_agent_assembly_survives_a_backend_lookup_failure(qtbot, monkeypatch):
    from pathlib import Path

    from lightfall.agents.spec import AgentSpec
    from lightfall.claude.agent import QtClaudeAgent
    from lightfall.ui.panels.claude.tool_registry import ToolRegistry

    monkeypatch.setattr("lightfall.claude.agent.ClaudeSDKClient", MagicMock())
    monkeypatch.setattr(ToolRegistry, "_read_list_pref", lambda self, key: None)
    monkeypatch.setattr(ToolRegistry, "_migrate_legacy_pref_if_needed", lambda self: None)
    ToolRegistry.reset_instance()

    def boom(*a, **k):
        raise RuntimeError("bad prefs")

    monkeypatch.setattr(info_mod, "backend_info", boom)
    spec = AgentSpec(name="lightfall", description="d", prompt="spec prompt", scope="core",
                     source_path=Path("lightfall.md"))
    target = QWidget()
    qtbot.addWidget(target)
    agent = QtClaudeAgent(target_window=target, require_approval=False, spec=spec)
    try:
        assert agent._spec is spec  # the spec path ran; no legacy fallback
        assert "## Your identity" in agent.options.system_prompt
        assert "spec prompt" in agent.options.system_prompt
    finally:
        agent.stop()
        ToolRegistry.reset_instance()
