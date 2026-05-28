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
