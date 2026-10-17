"""Cancel must not leave plain-text bubbles or spinning task cards behind."""
import pytest
from PySide6.QtWidgets import QVBoxLayout, QWidget

from lightfall.claude.widget import ClaudeAssistantWidget
from lightfall.claude.widgets.task_card import TaskCard


@pytest.fixture
def widget(qtbot, monkeypatch):
    w = ClaudeAssistantWidget.__new__(ClaudeAssistantWidget)
    QWidget.__init__(w)
    qtbot.addWidget(w)
    w._verbose_visible = False
    w._verbose_widgets = []
    w._streaming_bubbles = {}
    w._task_cards = {}
    w._task_tool_use_ids = {}
    w._at_bottom = False
    w._speaker = "Claude"
    container = QWidget(w)
    w._chat_layout = QVBoxLayout(container)
    w._scroll_to_bottom = lambda: None
    w._set_busy_state = lambda *a, **k: None
    return w


def test_cancel_renders_streaming_text_and_drops_empty(widget, monkeypatch):
    from lightfall.claude import markdown

    monkeypatch.setattr(markdown, "render_markdown", lambda t: f"<p>{t}</p>")
    widget._on_partial_block_started("t1", "text")
    widget._on_partial_text("t1", "half an answ")
    widget._on_partial_block_started("t2", "text")  # never got a delta
    label = widget._streaming_bubbles["t1"].label
    frame2 = widget._streaming_bubbles["t2"].frame

    widget._on_query_cancelled()

    assert widget._streaming_bubbles == {}
    assert label.text() == "<p>half an answ</p>"
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(frame2)  # the empty bubble was removed


def test_cancel_stops_running_task_cards(widget):
    widget._on_task_started("task-1", "survey the sample", "tu-1")
    done = TaskCard("task-2", "done already")
    done.mark_finished("completed", "ok", "", {})
    widget._task_cards["task-2"] = done

    widget._on_query_cancelled()

    assert widget._task_cards["task-1"].is_running is False
    assert widget._task_cards["task-1"]._status == "stopped"
    assert done._status == "completed"


def test_completion_flushes_bubbles_but_keeps_task_cards_running(widget):
    widget._on_partial_block_started("t1", "text")
    widget._on_partial_text("t1", "x")
    widget._on_task_started("task-1", "background run", "tu-1")

    widget._on_query_completed()

    assert widget._streaming_bubbles == {}
    assert widget._task_cards["task-1"].is_running is True
