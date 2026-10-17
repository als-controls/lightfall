"""A permission request that ends without an answer must say so, so its card goes away.

agy's hook client gives up after 285 s; the decision must come back before that,
and the approval card must not linger (a late "Allow" would do nothing).
"""
from __future__ import annotations

import asyncio

import pytest

from lightfall.agents.backends.agy import hook_policy
from lightfall.claude.permission_manager import PermissionManager


def _pm(qtbot):
    pm = PermissionManager()
    expired = []
    pm.permission_expired.connect(expired.append)
    return pm, expired


def test_cancelled_wait_cleans_up_and_emits_expired(qtbot):
    pm, expired = _pm(qtbot)

    async def run():
        task = asyncio.ensure_future(pm.request_permission("Bash", {"CommandLine": "dir"}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    qtbot.waitUntil(lambda: len(expired) == 1, timeout=1000)
    assert pm._pending_requests == {} and pm._pending_loops == {} and pm._responses == {}


def test_answered_request_does_not_emit_expired(qtbot):
    pm, expired = _pm(qtbot)
    pm.permission_requested.connect(lambda rid, *_: pm.respond(rid, True))

    async def run():
        return await pm.request_permission("Bash", {})

    assert asyncio.run(run()) == (True, "")
    qtbot.wait(50)
    assert expired == []


def test_agy_hook_decision_times_out_before_hook_client(monkeypatch, qtbot):
    from lightfall.agents.backends.agy import hook_client

    assert hook_policy.HOOK_DECISION_TIMEOUT_S < hook_client._TIMEOUT_S
    monkeypatch.setattr(hook_policy, "HOOK_DECISION_TIMEOUT_S", 0.1)
    pm, expired = _pm(qtbot)
    payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": "dir"}}}

    async def run():
        return await hook_policy.decide(payload, permission_manager=pm, require_approval=True,
                                        server_keys=set())

    r = asyncio.run(run())
    assert r == {"decision": "deny", "reason": "Approval timed out"}
    qtbot.waitUntil(lambda: len(expired) == 1, timeout=1000)


def test_widget_withdraws_expired_card(qtbot):
    from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

    from lightfall.claude.widget import ClaudeAssistantWidget

    w = ClaudeAssistantWidget.__new__(ClaudeAssistantWidget)
    QWidget.__init__(w)
    qtbot.addWidget(w)
    w._speaker = "Gemini"
    w._verbose_visible = False
    w._verbose_widgets = []
    w._at_bottom = False
    w._scroll_to_bottom = lambda: None
    chat = QWidget(w)
    w._chat_layout = QVBoxLayout(chat)
    w._permission_container = QWidget(w)
    w._permission_layout = QVBoxLayout(w._permission_container)
    w._pending_question_widgets = {}
    card = QLabel("approve?")
    w._permission_layout.addWidget(card)
    w._pending_permission_widgets = {"r1": card}
    w._pending_tool_names = {"r1": "Bash"}
    w.input_field = QPlainTextEdit()
    resolved = []
    w.approval_resolved.connect(lambda rid, ok: resolved.append((rid, ok)))

    w._on_permission_expired("r1")
    w._on_permission_expired("unknown")  # already gone: no-op

    assert w._pending_permission_widgets == {} and resolved == [("r1", False)]
    assert w._permission_container.isHidden()
