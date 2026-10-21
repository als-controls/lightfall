"""submit_user_prompt, the voice queue and strip, and the input accessory slot."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QPushButton, QToolButton, QWidget

from lightfall.agents.backends import info as info_mod
from lightfall.agents.backends.info import AuthStatus, BackendCaps, ModelOption
from lightfall.claude import widget as widget_mod
from lightfall.claude.voice_queue import VoiceQueue


class _Info:
    kind, display_name = "claude-code", "Claude"
    caps = BackendCaps(cost=False, live_set_model=False, subagents=True)

    def auth_status(self, refresh=False):
        return AuthStatus(True, "ok")

    def cached_models(self):
        return [ModelOption("", "Default")]

    def current_model(self):
        return ""

    def effort_options(self, model_id):
        return [""]

    def current_effort(self):
        return ""


class FakeAgent(QObject):
    message_received = Signal(str)
    thinking_received = Signal(str)
    tool_called = Signal(str, dict)
    tool_result = Signal(str, dict)
    error_occurred = Signal(str)
    query_completed = Signal()
    query_cancelled = Signal()
    permission_requested = Signal(str, str, dict)
    permission_expired = Signal(str)
    question_requested = Signal(str, list)
    partial_block_started = Signal(str, str)
    partial_text = Signal(str, str)
    partial_thinking = Signal(str, str)
    partial_block_finished = Signal(str)
    task_started = Signal(str, str, str)
    task_progress = Signal(str, str, dict, str)
    task_finished = Signal(str, str, str, str, dict)

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._agy = False
        self.busy = False
        self.sent: list[str] = []
        self.question_answers: list = []
        self.permission_answers: list = []

    def is_busy(self):
        return self.busy

    def query_sync(self, prompt):
        self.sent.append(prompt)
        self.busy = True

    def cancel(self):
        return self.busy

    def respond_to_question(self, request_id, answers):
        self.question_answers.append((request_id, answers))

    def respond_to_permission(self, request_id, allowed, **kw):
        self.permission_answers.append((request_id, allowed))

    def add_always_allowed_tool(self, name):
        pass

    def reset_conversation(self):
        self.busy = False

    # test helpers
    def complete(self):
        self.busy = False
        self.query_completed.emit()

    def fail(self, msg="boom"):
        self.busy = False
        self.error_occurred.emit(msg)

    def settle_cancel(self):
        self.busy = False
        self.query_cancelled.emit()


@pytest.fixture
def w(qtbot, monkeypatch):
    monkeypatch.setattr(widget_mod, "QtClaudeAgent", FakeAgent)
    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: _Info())
    target = QWidget()
    qtbot.addWidget(target)
    spec = SimpleNamespace(name="lightfall", description="d", on_message="queue")
    widget = widget_mod.ClaudeAssistantWidget(target_window=target, spec=spec)
    qtbot.addWidget(widget)
    return widget


def _strip(w):
    return w._voice_queue_strip


def _texts(w):
    return [it.text for it in w.voice_queue.items()]


def test_sends_when_idle_without_touching_draft(w):
    w.input_field.setPlainText("half-typed draft")
    assert w.submit_user_prompt("move the motor") == "sent"
    assert w.agent.sent == ["move the motor"]
    assert w.input_field.toPlainText() == "half-typed draft"
    assert len(w.voice_queue) == 0


def test_queues_when_busy_and_drains_fifo_on_completion(w):
    assert w.submit_user_prompt("one") == "sent"
    assert w.submit_user_prompt("two") == "queued"
    assert w.submit_user_prompt("three") == "queued"
    assert _texts(w) == ["two", "three"]
    assert not _strip(w).isHidden()

    w.agent.complete()
    assert w.agent.sent == ["one", "two"]
    assert _texts(w) == ["three"]

    w.agent.complete()
    assert w.agent.sent == ["one", "two", "three"]
    assert len(w.voice_queue) == 0
    assert _strip(w).isHidden()


def test_queues_while_permission_pending(w):
    w.agent.permission_requested.emit("r1", "Bash", {"command": "ls"})
    assert w.submit_user_prompt("hello") == "queued"
    assert w.agent.sent == []
    assert _texts(w) == ["hello"]


def test_answers_pending_question(w):
    w.submit_user_prompt("start")
    q = {"question": "Go?", "header": "H", "multiSelect": False,
         "options": [{"label": "Yes", "description": ""},
                     {"label": "No", "description": ""}]}
    w.agent.question_requested.emit("q1", [q])
    assert w.submit_user_prompt("yes") == "answered"
    assert w.agent.question_answers == [("q1", {"Go?": "Yes"})]
    assert w.agent.sent == ["start"]
    assert len(w.voice_queue) == 0


def test_stop_keeps_queue_and_drains_after_cancel(w):
    w.submit_user_prompt("one")
    w.submit_user_prompt("two")
    w.send_button.click()  # Stop
    assert w.submit_user_prompt("three") == "queued"  # cancelling counts as busy
    assert _texts(w) == ["two", "three"]

    w.agent.settle_cancel()
    assert w.agent.sent == ["one", "two"]
    assert _texts(w) == ["three"]


def test_error_pauses_queue_and_keeps_items(w):
    w.submit_user_prompt("one")
    w.submit_user_prompt("two")
    w.submit_user_prompt("three")
    w.agent.fail()
    w.agent.query_completed.emit()  # may or may not follow an error
    assert w.agent.sent == ["one"]
    assert w.voice_queue.paused
    assert _texts(w) == ["two", "three"]
    strip = _strip(w)
    assert not strip.isHidden()
    assert strip.paused_label.isVisibleTo(strip)
    assert strip.paused_label.text() == "Paused after error"
    button = strip.findChild(QPushButton, "voiceQueueSendQueued")
    assert button.text() == "Send queued"

    button.click()
    assert w.agent.sent == ["one", "two"]
    assert not w.voice_queue.paused
    assert _texts(w) == ["three"]
    assert not strip.paused_label.isVisibleTo(strip)


def test_successful_typed_turn_resumes_pause(w):
    w.submit_user_prompt("one")
    w.submit_user_prompt("two")
    w.agent.fail()
    assert w.voice_queue.paused

    w.input_field.setPlainText("typed")
    w._send_query()
    assert w.agent.sent == ["one", "typed"]
    w.agent.complete()
    assert not w.voice_queue.paused
    assert w.agent.sent == ["one", "typed", "two"]


def test_typed_send_while_paused_goes_first(w):
    w.submit_user_prompt("one")
    w.submit_user_prompt("two")
    w.agent.fail()

    w.input_field.setPlainText("typed")
    w.send_button.click()
    assert w.agent.sent == ["one", "typed"]
    assert _texts(w) == ["two"]
    assert w.input_field.toPlainText() == ""


def test_remove_item_via_x(w):
    w.submit_user_prompt("one")
    w.submit_user_prompt("two")
    w.submit_user_prompt("three")
    rows = _strip(w).item_rows()
    assert len(rows) == 2
    rows[0].findChild(QToolButton, "voiceQueueItemRemove").click()
    assert _texts(w) == ["three"]
    w.agent.complete()
    assert w.agent.sent == ["one", "three"]


def test_voice_queue_drains_before_bus_messages(w):
    w.bus_endpoint.set_policy("auto")
    w.submit_user_prompt("one")
    w.submit_user_prompt("voice two")
    w.bus_endpoint.deliver("other-agent", "bus hello")
    assert w.bus_endpoint.pending() == [("other-agent", "bus hello")]

    w.agent.complete()
    assert w.agent.sent == ["one", "voice two"]
    assert w.bus_endpoint.pending() == [("other-agent", "bus hello")]

    w.agent.complete()
    assert len(w.agent.sent) == 3
    assert "bus hello" in w.agent.sent[2]
    assert w.bus_endpoint.pending() == []


def test_add_input_accessory_after_send(w):
    acc = QPushButton("mic")
    w.add_input_accessory(acc)
    layout = w._input_layout
    assert layout.indexOf(acc) == layout.indexOf(w.send_button) + 1
    assert layout.indexOf(w.tune_button) == layout.indexOf(acc) + 1


def test_empty_text_ignored(w):
    assert w.submit_user_prompt("   \n ") == "ignored"
    assert w.agent.sent == []
    assert len(w.voice_queue) == 0


def test_set_voice_queue_rebinds_strip(w):
    q = VoiceQueue()
    q.push("carried over")
    w.set_voice_queue(q)
    assert w.voice_queue is q
    assert not _strip(w).isHidden()
    assert len(_strip(w).item_rows()) == 1


def test_placeholder_flash_restores(w, qtbot):
    before = w.input_field.placeholderText()
    w.placeholder_flash("Didn't catch that", ms=50)
    assert w.input_field.placeholderText() == "Didn't catch that"
    qtbot.waitUntil(lambda: w.input_field.placeholderText() == before, timeout=2000)


def test_stop_during_permission_card_does_not_jam_queue(w):
    w.submit_user_prompt("one")
    w.agent.permission_requested.emit("r1", "Bash", {"command": "ls"})
    assert w.submit_user_prompt("two") == "queued"
    w.send_button.click()  # Stop: the worker releases the wait without permission_expired
    w.agent.settle_cancel()
    assert w._pending_permission_widgets == {}
    assert w.agent.sent == ["one", "two"]
    w.agent.complete()
    assert w.submit_user_prompt("three") == "sent"


def test_resolving_permission_card_while_idle_drains_one(w):
    w.agent.permission_requested.emit("r1", "Bash", {"command": "ls"})
    w.submit_user_prompt("one")
    w.submit_user_prompt("two")
    assert w.agent.sent == []
    w._pending_permission_widgets["r1"].allowed.emit("r1", False)
    assert w.agent.sent == ["one"]
    assert _texts(w) == ["two"]


def test_handed_over_queue_survives_old_widget_deletion(qtbot, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent

    monkeypatch.setattr(widget_mod, "QtClaudeAgent", FakeAgent)
    monkeypatch.setattr(info_mod, "backend_info", lambda *a, **k: _Info())
    spec = SimpleNamespace(name="lightfall", description="d", on_message="queue")
    target = QWidget()
    qtbot.addWidget(target)
    a = widget_mod.ClaudeAssistantWidget(target_window=target, spec=spec)
    b = widget_mod.ClaudeAssistantWidget(target_window=target, spec=spec)
    qtbot.addWidget(b)
    queue = a.voice_queue
    b.set_voice_queue(queue)
    a.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()

    queue.push("still alive")
    assert b.voice_queue is queue
    assert not b._voice_queue_strip.isHidden()
    assert len(b._voice_queue_strip.item_rows()) == 1


def test_bus_prompt_is_stripped_and_empty_refused(w):
    assert w._submit_bus_prompt("   ") is False
    assert w._submit_bus_prompt("  hi  ") is True
    assert w.agent.sent == ["hi"]


def _two_question_card(w):
    qs = [
        {"question": "Go?", "header": "A", "multiSelect": False,
         "options": [{"label": "Yes", "description": ""},
                     {"label": "No", "description": ""}]},
        {"question": "Colour?", "header": "B", "multiSelect": False,
         "options": [{"label": "Red", "description": ""},
                     {"label": "Blue", "description": ""}]},
    ]
    w.agent.question_requested.emit("q2", qs)


def test_intermediate_answer_on_multi_question_card_is_consumed(w):
    w.submit_user_prompt("start")
    _two_question_card(w)
    assert w.submit_user_prompt("yes") == "answered"
    assert w.agent.sent == ["start"]
    assert len(w.voice_queue) == 0
    assert w.agent.question_answers == []

    assert w.submit_user_prompt("blue") == "answered"
    assert w.agent.question_answers == [("q2", {"Go?": "Yes", "Colour?": "Blue"})]
    assert w.agent.sent == ["start"]
    assert len(w.voice_queue) == 0


def test_text_rejected_by_card_is_dropped_not_sent(w):
    q = {"question": "Which?", "header": "M", "multiSelect": True,
         "options": [{"label": "Red", "description": ""},
                     {"label": "Blue", "description": ""}]}
    w.agent.question_requested.emit("q3", [q])
    assert w.submit_user_prompt("and") == "ignored"
    assert w.agent.sent == []
    assert len(w.voice_queue) == 0
    assert not w._pending_question_widgets["q3"].is_resolved


def test_held_queue_shows_release_row(w):
    w.voice_queue.hold()
    w.voice_queue.push("waiting")
    strip = _strip(w)
    assert not strip.isHidden()
    assert strip.paused_label.isVisibleTo(strip)
    assert strip.paused_label.text() == "Held: waiting for resumed session"
    button = strip.findChild(QPushButton, "voiceQueueSendQueued")
    assert button.isVisibleTo(strip)

    button.click()
    assert not w.voice_queue.held
    assert w.agent.sent == ["waiting"]
    assert len(w.voice_queue) == 0


def test_paused_label_wins_over_held(w):
    w.voice_queue.hold()
    w.voice_queue.pause()
    w.voice_queue.push("x")
    assert _strip(w).paused_label.text() == "Paused after error"


def test_placeholder_flash_after_busy_ends_restores_idle(w, qtbot):
    idle = w.input_field.placeholderText()
    w.submit_user_prompt("one")  # busy: placeholder shows the status
    busy_text = w.input_field.placeholderText()
    assert busy_text != idle
    w.placeholder_flash("Transcribing…", ms=10_000)
    w.agent.complete()  # busy ends during the flash
    assert w.input_field.placeholderText() == idle
    w.placeholder_flash("Didn't catch that", ms=50)  # e.g. a cancel after the turn
    qtbot.waitUntil(lambda: w.input_field.placeholderText() == idle, timeout=2000)
    qtbot.wait(100)
    assert w.input_field.placeholderText() == idle
