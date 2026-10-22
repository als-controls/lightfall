"""Autoscroll must respect the user's scroll position.

Regression: _add_widget scrolled to the bottom unconditionally, yanking a user
who had scrolled up back down whenever a new message arrived. It should only
follow the tail when the view is already at the bottom.
"""

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from lightfall.claude.widget import ClaudeAssistantWidget


@pytest.fixture
def widget(qtbot):
    w = ClaudeAssistantWidget.__new__(ClaudeAssistantWidget)
    QWidget.__init__(w)
    qtbot.addWidget(w)
    w._verbose_visible = True
    w._verbose_widgets = []
    w._streaming_bubbles = {}
    container = QWidget(w)
    w._chat_layout = QVBoxLayout(container)
    w._scroll_calls = 0

    def _scroll():
        w._scroll_calls += 1

    w._scroll_to_bottom = _scroll  # count scroll requests
    return w


def _flush():
    # _scroll_to_bottom_if_needed defers via QTimer.singleShot(0, ...)
    QApplication.processEvents()


def test_add_widget_scrolls_when_at_bottom(widget, qtbot):
    widget._at_bottom = True
    widget._add_widget(QLabel("msg"))
    _flush()
    assert widget._scroll_calls == 1


def test_add_widget_does_not_scroll_when_scrolled_up(widget, qtbot):
    widget._at_bottom = False
    widget._add_widget(QLabel("msg"))
    _flush()
    assert widget._scroll_calls == 0
    # The widget is still added to the transcript, just not scrolled to.
    assert widget._chat_layout.count() == 1
