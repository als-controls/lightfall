"""PermissionManager.request_question / respond_to_question."""
from __future__ import annotations

import asyncio

import pytest

from lucid.claude.permission_manager import PermissionManager


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
    from lucid.claude.permission_manager import (
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
    from lucid.claude.permission_manager import (
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
    from lucid.claude.permission_manager import (
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
