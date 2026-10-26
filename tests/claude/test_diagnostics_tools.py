"""Tests for the get_diagnostics / draft_bug_report agent tools."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from lightfall.claude.tools.diagnostics import (
    _SESSION_MARKER,
    create_diagnostics_tools,
    read_fault_log,
)


def _tools(window=None):
    return {t.name: t for t in create_diagnostics_tools(window)}


def _payload(result):
    return json.loads(result["content"][0]["text"])


def test_fault_log_unavailable_when_not_installed():
    assert read_fault_log(None)["available"] is False


def test_fault_log_missing_file(tmp_path):
    out = read_fault_log(tmp_path / "fault.log")
    assert out["available"] is False and "no fault.log" in out["reason"]


def test_fault_log_splits_sessions_and_flags_previous_crash(tmp_path):
    path = tmp_path / "fault.log"
    path.write_text(
        f"\n{_SESSION_MARKER}2026-10-24T09:00 ---\nFatal Python error: Segmentation fault\n"
        "Thread 0x01 (most recent call first):\n  File \"x.py\", line 1\n"
        f"\n{_SESSION_MARKER}2026-10-25T09:00 ---\n",
        encoding="utf-8",
    )
    out = read_fault_log(path)
    assert out["previous_session_crashed"] is True
    assert "Segmentation fault" in out["previous_session"]
    assert out["current_session"].startswith(_SESSION_MARKER + "2026-10-25")
    assert "Segmentation" not in out["current_session"]


def test_fault_log_clean_previous_session(tmp_path):
    path = tmp_path / "fault.log"
    path.write_text(
        f"{_SESSION_MARKER}a ---\n{_SESSION_MARKER}b ---\n", encoding="utf-8"
    )
    assert read_fault_log(path)["previous_session_crashed"] is False


def test_fault_log_truncates_long_sections(tmp_path):
    path = tmp_path / "fault.log"
    path.write_text(f"{_SESSION_MARKER}a ---\n" + "x" * 5000, encoding="utf-8")
    out = read_fault_log(path, max_chars=100)
    assert len(out["current_session"]) == 101 and out["current_session"].startswith("…")


def test_get_diagnostics_reports_recent_errors(monkeypatch, tmp_path):
    from lightfall.utils.error_collector import ErrorRecord

    rec = ErrorRecord(timestamp=datetime.now(UTC), level="ERROR", module="m",
                      function="f", line=3, message="boom", exception_info="Traceback…")

    class _Collector:
        def get_recent_errors(self, max_count=None):
            return [rec]

    monkeypatch.setattr("lightfall.utils.error_collector.get_error_collector", lambda: _Collector())
    monkeypatch.setattr("lightfall.utils.crash_diagnostics._diag_dir", tmp_path)
    out = _payload(asyncio.run(_tools()["get_diagnostics"].handler({})))
    assert out["recent_errors"][0]["message"] == "boom"
    assert out["recent_errors"][0]["location"] == "m:f:3"
    assert out["fault_log"]["available"] is False
    assert "lightfall" in out["version"]


def test_draft_bug_report_requires_description():
    result = asyncio.run(_tools()["draft_bug_report"].handler({"description": "  "}))
    assert result["is_error"] is True


def test_draft_bug_report_opens_prefilled_dialog(qtbot, monkeypatch):
    from lightfall.ui.dialogs.bug_report_dialog import BugPriority, BugReportDialog
    from lightfall.utils.error_collector import ErrorRecord

    rec = ErrorRecord(timestamp=datetime.now(UTC), level="ERROR", module="m",
                      function="f", line=3, message="boom", id="err-1")

    class _Collector:
        def get_recent_errors(self, max_count=None):
            return [rec]

    monkeypatch.setattr("lightfall.utils.error_collector.get_error_collector", lambda: _Collector())
    opened = []
    monkeypatch.setattr(BugReportDialog, "open", lambda self: opened.append(self))

    result = asyncio.run(_tools()["draft_bug_report"].handler({
        "description": "Panel froze after reconnect.", "priority": "high", "error_id": "err-1",
    }))
    assert _payload(result) == {
        "opened": True, "error_attached": True,
        "note": "The dialog is open for the user to review and submit.",
    }
    (dialog,) = opened
    qtbot.addWidget(dialog)
    assert dialog._description_text.toPlainText() == "Panel froze after reconnect."
    assert dialog._priority_buttons[BugPriority.HIGH].isChecked()
    assert dialog._selected_error is rec


@pytest.mark.parametrize("name", ["get_diagnostics", "draft_bug_report"])
def test_tools_are_on_the_qt_server(name):
    from lightfall.claude.tools import create_qt_tools_server

    server = create_qt_tools_server(None)
    assert name in {t.name for t in server.tools}
