"""Thinking / tool-usage fragment visibility toggle (default hidden).

Builds a partial ClaudeAssistantWidget (no agent) — just the chat surface the
fragment builders need — mirroring the __new__ harness style used elsewhere.
"""

import pytest
from PySide6.QtWidgets import QVBoxLayout, QWidget

from lightfall.claude.widget import ClaudeAssistantWidget


@pytest.fixture
def widget(qtbot):
    w = ClaudeAssistantWidget.__new__(ClaudeAssistantWidget)
    QWidget.__init__(w)
    qtbot.addWidget(w)
    w._verbose_visible = False
    w._verbose_widgets = []
    w._streaming_bubbles = {}
    w._at_bottom = False
    container = QWidget(w)
    w._chat_layout = QVBoxLayout(container)
    w._scroll_to_bottom = lambda: None  # no scroll area in the harness
    return w


def _chat_widgets(w):
    return [w._chat_layout.itemAt(i).widget() for i in range(w._chat_layout.count())]


def test_thinking_card_hidden_by_default(widget):
    widget._append_thinking_message("pondering...")
    (card,) = _chat_widgets(widget)
    assert card.isHidden()


def test_tool_usage_hidden_by_default(widget):
    widget._on_tool_called("Read", {"file_path": "x"})
    (lbl,) = _chat_widgets(widget)
    assert lbl.isHidden()


def test_task_tool_adds_no_fragment(widget):
    widget._on_tool_called("Task", {})
    assert widget._chat_layout.count() == 0


def test_toggle_shows_then_hides_fragments(widget):
    widget._append_thinking_message("hmm")
    widget._on_tool_called("Read", {})
    widget.set_verbose_visible(True)
    assert all(not w.isHidden() for w in _chat_widgets(widget))
    widget.set_verbose_visible(False)
    assert all(w.isHidden() for w in _chat_widgets(widget))


def test_new_fragment_respects_visible_state(widget):
    widget.set_verbose_visible(True)
    widget._append_thinking_message("live")
    (card,) = _chat_widgets(widget)
    assert not card.isHidden()


def test_streaming_thinking_bubble_tracked(widget):
    widget._on_partial_block_started("b1", "thinking")
    (frame,) = _chat_widgets(widget)
    assert frame.isHidden()
    # Empty bubble teardown drops it from tracking too.
    widget._on_partial_block_finished("b1")
    assert widget._verbose_widgets == []


def test_regular_messages_unaffected(widget):
    widget._append_system_message("Conversation reset")
    (lbl,) = _chat_widgets(widget)
    assert not lbl.isHidden()
