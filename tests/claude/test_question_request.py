"""PermissionManager.request_question / respond_to_question."""
from __future__ import annotations

import asyncio

import pytest

from lightfall.claude.permission_manager import PermissionManager


@pytest.mark.asyncio
async def test_request_question_emits_signal_and_returns_answers(qtbot):
    pm = PermissionManager()
    captured: list[tuple[str, list]] = []
    pm.question_requested.connect(
        lambda rid, qs: captured.append((rid, list(qs)))
    )

    task = asyncio.create_task(
        pm.request_question([{"question": "Which DB?", "options": []}])
    )
    # Let the signal propagate through the event loop.
    for _ in range(20):
        if captured:
            break
        await asyncio.sleep(0.01)
    assert captured, "question_requested signal was not emitted"
    request_id, questions = captured[0]
    assert questions == [{"question": "Which DB?", "options": []}]

    pm.respond_to_question(request_id, {"Which DB?": "PostgreSQL"})

    answered, answers = await asyncio.wait_for(task, timeout=1.0)
    assert answered is True
    assert answers == {"Which DB?": "PostgreSQL"}


@pytest.mark.asyncio
async def test_respond_with_none_means_cancelled(qtbot):
    pm = PermissionManager()
    captured: list[str] = []
    pm.question_requested.connect(lambda rid, _qs: captured.append(rid))

    task = asyncio.create_task(pm.request_question([{"question": "Q?"}]))
    for _ in range(20):
        if captured:
            break
        await asyncio.sleep(0.01)
    pm.respond_to_question(captured[0], None)

    answered, answers = await asyncio.wait_for(task, timeout=1.0)
    assert answered is False
    assert answers == {}


@pytest.mark.asyncio
async def test_cancel_all_pending_wakes_question(qtbot):
    pm = PermissionManager()
    captured: list[str] = []
    pm.question_requested.connect(lambda rid, _qs: captured.append(rid))

    task = asyncio.create_task(pm.request_question([{"question": "Q?"}]))
    for _ in range(20):
        if captured:
            break
        await asyncio.sleep(0.01)
    pm.cancel_all_pending()

    answered, answers = await asyncio.wait_for(task, timeout=1.0)
    assert answered is False
    assert answers == {}


@pytest.mark.asyncio
async def test_can_use_tool_routes_AskUserQuestion_to_request_question(qtbot):
    from claude_agent_sdk import (
        PermissionResultAllow,
        PermissionResultDeny,
        ToolPermissionContext,
    )
    from lightfall.claude.permission_manager import (
        PermissionManager,
        create_can_use_tool_callback,
    )

    pm = PermissionManager()
    callback = create_can_use_tool_callback(pm)
    questions = [{"question": "Which?", "options": [{"label": "A"}], "multiSelect": False}]

    # Respond before awaiting; we'll connect a signal handler that calls
    # respond_to_question as soon as the request is emitted.
    def _auto_answer(request_id: str, _qs: list) -> None:
        pm.respond_to_question(request_id, {"Which?": "A"})

    pm.question_requested.connect(_auto_answer)

    result = await callback(
        "AskUserQuestion",
        {"questions": questions},
        ToolPermissionContext(),
    )

    assert isinstance(result, PermissionResultAllow)
    assert result.updated_input == {
        "questions": questions,
        "answers": {"Which?": "A"},
    }


@pytest.mark.asyncio
async def test_can_use_tool_AskUserQuestion_cancel_denies(qtbot):
    from claude_agent_sdk import PermissionResultDeny, ToolPermissionContext
    from lightfall.claude.permission_manager import (
        PermissionManager,
        create_can_use_tool_callback,
    )

    pm = PermissionManager()
    callback = create_can_use_tool_callback(pm)

    def _auto_cancel(request_id: str, _qs: list) -> None:
        pm.respond_to_question(request_id, None)

    pm.question_requested.connect(_auto_cancel)

    result = await callback(
        "AskUserQuestion",
        {"questions": [{"question": "Q?"}]},
        ToolPermissionContext(),
    )

    assert isinstance(result, PermissionResultDeny)


@pytest.mark.asyncio
async def test_pre_tool_use_hook_forces_ask_for_AskUserQuestion(qtbot):
    """Production bug: returning empty dict from the hook lets the SDK
    fall back to its CLI default, which in headless mode silently
    dismisses AskUserQuestion without routing through can_use_tool.
    Explicitly returning permissionDecision="ask" forces the SDK to
    invoke can_use_tool, where we render the question UI."""
    from lightfall.claude.permission_manager import (
        PermissionManager,
        create_pre_tool_use_hook,
    )

    pm = PermissionManager()
    hook = create_pre_tool_use_hook(pm)

    result = await hook(
        {"tool_name": "AskUserQuestion", "tool_input": {"questions": []}},
        "tool_use_id_xyz",
        None,
    )

    assert result == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
        },
    }


@pytest.mark.asyncio
async def test_pre_tool_use_hook_bypass_mode_still_asks_for_AskUserQuestion(qtbot):
    """With require_approval=False (bypassPermissions), normal tools fall
    through to the CLI's auto-allow — but AskUserQuestion must STILL be
    routed through can_use_tool, because it's an interactive tool, not
    a permission gate. This was the bug the user hit: their settings
    had bypassPermissions, so the hook was never registered, so the
    CLI auto-dismissed AskUserQuestion."""
    from lightfall.claude.permission_manager import (
        PermissionManager,
        create_pre_tool_use_hook,
    )

    pm = PermissionManager()
    hook = create_pre_tool_use_hook(pm, require_approval=False)

    # AskUserQuestion still forces ask
    result = await hook(
        {"tool_name": "AskUserQuestion", "tool_input": {"questions": []}},
        "tu-1", None,
    )
    assert result == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
        },
    }

    # Normal tool falls through to CLI default (no decision)
    result = await hook(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        "tu-2", None,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_can_use_tool_bypass_mode_still_handles_AskUserQuestion(qtbot):
    """In bypass mode (require_approval=False), normal tools auto-allow,
    but AskUserQuestion still routes through the question UI."""
    from claude_agent_sdk import (
        PermissionResultAllow,
        ToolPermissionContext,
    )
    from lightfall.claude.permission_manager import (
        PermissionManager,
        create_can_use_tool_callback,
    )

    pm = PermissionManager()
    callback = create_can_use_tool_callback(pm, require_approval=False)

    # Normal tool auto-allowed (no question_requested signal needed).
    result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())
    assert isinstance(result, PermissionResultAllow)
    assert result.updated_input is None

    # AskUserQuestion still goes through the question UI.
    questions = [{"question": "OK?", "options": [{"label": "Yes"}]}]
    pm.question_requested.connect(
        lambda rid, _qs: pm.respond_to_question(rid, {"OK?": "Yes"})
    )
    result = await callback(
        "AskUserQuestion", {"questions": questions}, ToolPermissionContext(),
    )
    assert isinstance(result, PermissionResultAllow)
    assert result.updated_input == {
        "questions": questions, "answers": {"OK?": "Yes"},
    }
