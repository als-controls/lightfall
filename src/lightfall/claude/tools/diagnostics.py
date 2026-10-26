"""Self-diagnosis tools for the embedded agent.

``get_diagnostics`` gathers what a developer would look at first when
Lightfall misbehaves: the ErrorCollector's recent ERROR+ records (with
tracebacks), the tail of ``fault.log`` (segfault / hang thread dumps,
split into this session and the previous one so a crash on the last run
is visible), and basic version info.

``draft_bug_report`` opens the existing Report-a-Bug dialog pre-filled
with the agent's write-up. The human reviews, edits and submits it; the
agent never sends a report itself.
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import Any

_SESSION_MARKER = "--- crash_diagnostics installed at "
_FAULT_TAIL_BYTES = 256 * 1024
_DEFAULT_MAX_CHARS = 8000
_CRASH_SIGNATURES = ("Fatal Python error", "Windows fatal exception", "Segmentation fault")


def _text(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]}


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


def _fault_log_path() -> Path | None:
    from lightfall.utils import crash_diagnostics

    diag_dir = crash_diagnostics._diag_dir
    return diag_dir / "fault.log" if diag_dir is not None else None


def _tail(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else "…" + text[-max_chars:]


def read_fault_log(path: Path | None, max_chars: int = _DEFAULT_MAX_CHARS) -> dict[str, Any]:
    """This session's and the previous session's fault.log sections.

    Each Lightfall start appends a marker line, so the section before the
    current marker holds whatever the previous run wrote, including the
    thread dump of a crash that killed it."""
    if path is None:
        return {"available": False, "reason": "crash diagnostics are not installed"}
    if not path.exists():
        return {"available": False, "path": str(path), "reason": "no fault.log yet"}
    with path.open("rb") as fh:
        size = fh.seek(0, 2)
        fh.seek(max(0, size - _FAULT_TAIL_BYTES))
        text = fh.read().decode("utf-8", errors="replace")
    sections = text.split(_SESSION_MARKER)
    current = _SESSION_MARKER + sections[-1] if len(sections) > 1 else sections[-1]
    previous = _SESSION_MARKER + sections[-2] if len(sections) > 2 else ""
    return {
        "available": True,
        "path": str(path),
        "current_session": _tail(current.strip(), max_chars),
        "previous_session": _tail(previous.strip(), max_chars),
        "previous_session_crashed": any(sig in previous for sig in _CRASH_SIGNATURES),
    }


def recent_errors(max_count: int) -> list[dict[str, Any]]:
    from lightfall.utils.error_collector import get_error_collector

    return [
        {
            "id": e.id,
            "time": e.timestamp.isoformat(),
            "level": e.level,
            "location": e.location,
            "message": e.message,
            "traceback": e.exception_info,
        }
        for e in get_error_collector().get_recent_errors(max_count=max_count)
    ]


def _version_info() -> dict[str, Any]:
    try:
        from lightfall import __version__ as lf_version
    except Exception:  # noqa: BLE001
        lf_version = "unknown"
    return {
        "lightfall": lf_version,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def create_diagnostics_tools(target_window) -> list:
    from lightfall.agents.tooling import tool

    @tool(
        name="get_diagnostics",
        description=(
            "Collect Lightfall's own health information: recent ERROR/CRITICAL "
            "records with tracebacks (from the error collector), the tail of "
            "fault.log for this session and the previous one (segfault and "
            "hang thread dumps; previous_session_crashed tells you whether the "
            "last run died), and version info. Use it when Lightfall itself "
            "misbehaves, before guessing. For general log lines use "
            "get_recent_logs."
        ),
        input_schema={
            "max_errors": {
                "type": "number",
                "description": "Maximum recent errors to return (default 20, max 50).",
                "default": 20,
            },
            "max_chars": {
                "type": "number",
                "description": (
                    f"Maximum characters per fault.log section (default {_DEFAULT_MAX_CHARS})."
                ),
                "default": _DEFAULT_MAX_CHARS,
            },
        },
    )
    async def get_diagnostics(args: dict) -> dict[str, Any]:
        try:
            max_errors = max(1, min(int(args.get("max_errors", 20)), 50))
            max_chars = max(500, int(args.get("max_chars", _DEFAULT_MAX_CHARS)))
            return _text({
                "version": _version_info(),
                "recent_errors": recent_errors(max_errors),
                "fault_log": read_fault_log(_fault_log_path(), max_chars),
            })
        except Exception as e:  # noqa: BLE001
            return _error(f"get_diagnostics error: {e}")

    @tool(
        name="draft_bug_report",
        description=(
            "Open Lightfall's Report-a-Bug dialog pre-filled with your write-up, "
            "for the user to review, edit and submit. You cannot submit it "
            "yourself. Write the description for a developer: what the user "
            "was doing, what happened, what was expected, and the evidence "
            "(error ids, tracebacks, fault.log excerpts) from get_diagnostics. "
            "Ask the user before opening it."
        ),
        input_schema={
            "description": {
                "type": "string",
                "description": "The bug report body (plain text or Markdown).",
            },
            "priority": {
                "type": "string",
                "description": "LOW, NORMAL, HIGH or CRITICAL. Default NORMAL.",
                "default": "NORMAL",
            },
            "error_id": {
                "type": "string",
                "description": "Optional id of a recent error (from get_diagnostics) to attach.",
            },
        },
    )
    async def draft_bug_report(args: dict) -> dict[str, Any]:
        description = str(args.get("description") or "").strip()
        if not description:
            return _error("draft_bug_report: 'description' is required")
        priority = str(args.get("priority") or "NORMAL").upper()
        error_id = args.get("error_id") or None
        try:
            from lightfall.claude._internal.threading import run_on_main_thread

            attached = run_on_main_thread(
                _open_prefilled_dialog, target_window, description, priority, error_id
            )
            return _text({
                "opened": True,
                "error_attached": attached,
                "note": "The dialog is open for the user to review and submit.",
            })
        except Exception as e:  # noqa: BLE001
            return _error(f"draft_bug_report error: {e}")

    return [get_diagnostics, draft_bug_report]


def _open_prefilled_dialog(parent, description: str, priority: str, error_id: str | None) -> bool:
    from PySide6.QtCore import Qt

    from lightfall.ui.dialogs.bug_report_dialog import BugReportDialog

    dialog = BugReportDialog(parent)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    attached = dialog.prefill(description, priority=priority, error_id=error_id)
    dialog.open()
    return attached
