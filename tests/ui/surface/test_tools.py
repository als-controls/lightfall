"""The agent-facing tools: ui_snapshot, ui_act, ui_wait, scoped screenshot."""

import asyncio
import json

import pytest
from PySide6.QtWidgets import QLineEdit, QMainWindow, QPushButton, QVBoxLayout, QWidget

from lightfall.ui.surface.tools import create_surface_tools
from tests.ui.surface.test_act import in_worker


@pytest.fixture
def window(qtbot):
    w = QMainWindow()
    w.setWindowTitle("Lightfall")
    body = QWidget()
    lay = QVBoxLayout(body)
    w.edit = QLineEdit()
    w.edit.setAccessibleName("Title")
    w.go = QPushButton("Go")
    lay.addWidget(w.edit)
    lay.addWidget(w.go)
    w.setCentralWidget(body)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    return w


def _call(qtbot, tool, args):
    return in_worker(qtbot, lambda: asyncio.run(tool.handler(args)))


def _text(result):
    return result["content"][0]["text"]


def test_tool_names_and_required_args():
    snap, act_, wait_ = create_surface_tools()
    assert (snap.name, act_.name, wait_.name) == ("ui_snapshot", "ui_act", "ui_wait")
    assert act_.input_schema["required"] == ["action"]
    assert "fill" in act_.input_schema["properties"]["action"]["enum"]


def test_snapshot_tool(window, qtbot):
    snap, _, _ = create_surface_tools()
    r = _call(qtbot, snap, {})
    assert 'field "Title"' in _text(r) and 'button "Go"' in _text(r)


def test_act_tool_by_role_and_name(window, qtbot):
    _, act_, _ = create_surface_tools()
    r = _call(qtbot, act_, {"action": "fill", "role": "field", "name": "Title", "value": "hi"})
    assert not r.get("is_error")
    assert json.loads(_text(r))["ok"] is True
    assert window.edit.text() == "hi"


def test_act_tool_error_is_short(window, qtbot):
    _, act_, _ = create_surface_tools()
    r = _call(qtbot, act_, {"action": "click", "role": "button", "name": "Stop"})
    assert r["is_error"]
    assert "Traceback" not in _text(r) and "no button" in _text(r)


def test_unknown_action_is_an_error(window, qtbot):
    _, act_, _ = create_surface_tools()
    r = _call(qtbot, act_, {"action": "teleport", "name": "Go"})
    assert r["is_error"] and "unknown action" in _text(r)


def test_wait_tool(window, qtbot):
    _, _, wait_ = create_surface_tools()
    r = _call(qtbot, wait_, {"condition": "element", "name": "Go", "timeout_s": 2})
    assert json.loads(_text(r))["met"] is True


def test_screenshot_scope(window, qtbot):
    from lightfall.claude.tools.screenshot import create_screenshot_tool

    tool = create_screenshot_tool(window)
    whole = _call(qtbot, tool, {})
    assert whole["content"][0]["type"] == "image"
    missing = _call(qtbot, tool, {"scope": "No Such Panel"})
    assert missing["is_error"] and "Traceback" not in _text(missing)
