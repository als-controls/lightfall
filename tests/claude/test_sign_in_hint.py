"""A signed-out agy session shows a sign-in hint instead of failing at the first turn."""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QWidget

from lightfall.agents.backends import info as info_mod
from lightfall.agents.backends.info import AuthStatus, BackendCaps, ModelOption
from lightfall.claude import widget as widget_mod

HINT = "assistantSignInHint"


SIGNED_OUT = AuthStatus(False, "Not signed in: x")


class _Info:
    def __init__(self, kind="agy", name="Gemini", status=SIGNED_OUT):
        self.kind, self.display_name, self.status = kind, name, status
        self.caps = BackendCaps(cost=False, live_set_model=False, subagents=True)
        self.calls = []  # (refresh, thread ident)

    def auth_status(self, refresh=False):
        self.calls.append((refresh, threading.get_ident()))
        return self.status

    def cached_models(self):
        return [ModelOption("", "Default")]

    def current_model(self):
        return ""

    def effort_options(self, model_id):
        return [""]

    def current_effort(self):
        return ""


def _build(qtbot, monkeypatch, desc):
    monkeypatch.setattr(widget_mod, "QtClaudeAgent", MagicMock())
    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: desc)
    target = QWidget()
    qtbot.addWidget(target)
    spec = SimpleNamespace(name="lightfall", description="d", on_message="queue")
    w = widget_mod.ClaudeAssistantWidget(target_window=target, spec=spec)
    qtbot.addWidget(w)
    return w


def _hint(w):
    return w.findChild(QFrame, HINT)


def test_signed_out_agy_shows_hint_checked_off_the_gui_thread(qtbot, monkeypatch):
    desc = _Info()
    w = _build(qtbot, monkeypatch, desc)
    qtbot.waitUntil(lambda: _hint(w) is not None, timeout=3000)
    text = " ".join(lb.text() for lb in _hint(w).findChildren(QLabel))
    assert "Sign in to Gemini in Settings → Assistant" in text
    assert "Not signed in: x" in text
    assert desc.calls and desc.calls[0][1] != threading.get_ident()


def test_signed_in_agy_shows_no_hint(qtbot, monkeypatch):
    desc = _Info(status=AuthStatus(True, "Signed in (3 models)"))
    w = _build(qtbot, monkeypatch, desc)
    qtbot.waitUntil(lambda: desc.calls != [], timeout=3000)
    qtbot.waitUntil(lambda: not w._auth_future.isRunning(), timeout=3000)
    qtbot.wait(50)
    assert _hint(w) is None


def test_claude_backend_is_not_checked(qtbot, monkeypatch):
    desc = _Info(kind="claude-code", name="Claude")
    w = _build(qtbot, monkeypatch, desc)
    qtbot.wait(100)
    assert desc.calls == [] and _hint(w) is None


def test_hint_button_opens_assistant_settings_and_rechecks(qtbot, monkeypatch):
    from lightfall.ui.preferences import dialog as dialog_mod

    desc = _Info()
    w = _build(qtbot, monkeypatch, desc)
    qtbot.waitUntil(lambda: _hint(w) is not None, timeout=3000)
    opened = []

    class _Dialog:
        def __init__(self, parent=None, initial_page=None):
            opened.append(initial_page)

        def exec(self):
            desc.status = AuthStatus(True, "Signed in (3 models)")  # user signed in
            return 1

    monkeypatch.setattr(dialog_mod, "PreferencesDialog", _Dialog)
    _hint(w).findChild(QPushButton).click()
    assert opened == ["claude"]
    qtbot.waitUntil(lambda: _hint(w) is None, timeout=3000)
    assert desc.calls[-1][0] is True  # the re-check bypasses the cache


def test_late_result_after_widget_is_gone_is_ignored(qtbot, monkeypatch):
    gate = threading.Event()

    class _Slow(_Info):
        def auth_status(self, refresh=False):
            gate.wait(3)
            return super().auth_status(refresh)

    desc = _Slow()
    w = _build(qtbot, monkeypatch, desc)
    future = w._auth_future
    w._auth_generation += 1  # superseded (as on a re-check or teardown)
    gate.set()
    qtbot.waitUntil(lambda: not future.isRunning(), timeout=3000)
    qtbot.wait(50)
    assert _hint(w) is None


def test_a_completed_turn_clears_the_hint(qtbot, monkeypatch):
    from unittest.mock import call

    w = _build(qtbot, monkeypatch, _Info())
    qtbot.waitUntil(lambda: _hint(w) is not None, timeout=3000)
    assert call(w._clear_sign_in_hint) in w.agent.query_completed.connect.call_args_list
    w._clear_sign_in_hint()
    qtbot.waitUntil(lambda: _hint(w) is None, timeout=3000)
