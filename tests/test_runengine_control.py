"""Tests for RunEngineControlWidget UI state logic."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QToolButton

from lightfall.ui.widgets.runengine_control import RunEngineControlWidget


@pytest.fixture
def widget(qtbot):
    w = RunEngineControlWidget()
    qtbot.addWidget(w)
    return w


def _engine(state: str, queue: int) -> SimpleNamespace:
    return SimpleNamespace(state_name=state, queue_size=queue)


def test_controls_are_icon_buttons(widget):
    assert isinstance(widget._pause_resume_btn, QToolButton)
    assert isinstance(widget._stop_btn, QToolButton)
    assert isinstance(widget._abort_btn, QToolButton)
    # No surrounding status frame anymore — spinner/label are direct children.
    assert widget._status_indicator.parent() is widget
    assert widget._status_label.parent() is widget


def test_queue_label_hidden_below_two(widget, qtbot):
    widget._engine = _engine("idle", 1)
    widget._update_state()
    assert widget._queue_label.isVisible() is False


def test_queue_label_visible_at_two(widget, qtbot):
    widget.show()  # visibility is only meaningful once shown
    qtbot.waitExposed(widget)
    widget._engine = _engine("idle", 2)
    widget._update_state()
    assert widget._queue_label.isVisible() is True
    assert widget._queue_label.text() == "Queue: 2"


def test_no_engine_hides_queue_and_disables(widget):
    widget._engine = None
    widget._update_state()
    assert widget._queue_label.isVisible() is False
    assert widget._pause_resume_btn.isEnabled() is False
    assert widget._stop_btn.isEnabled() is False
    assert widget._abort_btn.isEnabled() is False


def test_running_enables_controls_pause_tooltip(widget):
    widget._engine = _engine("running", 0)
    widget._update_state()
    assert widget._pause_resume_btn.isEnabled() is True
    assert widget._stop_btn.isEnabled() is True
    assert widget._abort_btn.isEnabled() is True
    assert "Pause" in widget._pause_resume_btn.toolTip()


def test_paused_shows_resume_tooltip(widget):
    widget._engine = _engine("paused", 0)
    widget._update_state()
    assert widget._pause_resume_btn.isEnabled() is True
    assert "Resume" in widget._pause_resume_btn.toolTip()
