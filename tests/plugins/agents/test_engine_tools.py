"""Tests for the ncs_wait_for_idle MCP tool helper.

Mirrors test_beam_status.py's style: tests the module-level payload helper
directly so we don't need claude_agent_sdk installed in CI.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import lucid.plugins.agents.engine_tools as et
from lucid.acquire.engine.state import EngineState


class _FakeEngine:
    """Engine stub: yields a scripted sequence of states, then sticks on the last.

    Each call to ``state`` / ``is_idle`` consumes one entry from the script
    until exhausted, then keeps returning the last one. That matches "RUNNING
    for N polls then IDLE" — the helper polls until it sees IDLE.
    """

    def __init__(self, states: list[EngineState]) -> None:
        assert states, "need at least one state"
        self._script = list(states)
        self._idx = 0

    def _peek(self) -> EngineState:
        if self._idx < len(self._script):
            s = self._script[self._idx]
            self._idx += 1
            return s
        return self._script[-1]

    @property
    def state(self) -> EngineState:
        return self._peek()

    @property
    def is_idle(self) -> bool:
        # Re-uses _peek so each tick advances together with state reads.
        # Helper code reads is_idle once per poll inside a single closure,
        # so we keep peek() coupled to that one read.
        return self._peek() == EngineState.IDLE


def _patch_engine(monkeypatch, engine):
    """Make ``from lucid.acquire.engine import get_engine`` return ``engine``."""
    import lucid.acquire.engine as engine_pkg

    monkeypatch.setattr(engine_pkg, "get_engine", lambda: engine, raising=True)


def _patch_last_run(monkeypatch, payload: dict[str, Any] | None):
    """Stub the helper that fetches last-run metadata.

    The wait helper calls ``_last_run_payload()`` internally to populate
    ``last_run``; tests stub that to avoid hitting Tiled.
    """
    if payload is None:
        monkeypatch.setattr(et, "_last_run_payload", lambda: None, raising=True)
    else:
        monkeypatch.setattr(et, "_last_run_payload", lambda: dict(payload), raising=True)


def test_wait_returns_immediately_when_already_idle(monkeypatch):
    """If the engine is idle on the very first poll, elapsed ≈ 0."""
    _patch_engine(monkeypatch, _FakeEngine([EngineState.IDLE]))
    _patch_last_run(monkeypatch, {"uid": "abc", "plan_name": "count"})

    result = asyncio.run(
        et._wait_for_idle_payload(
            timeout_seconds=5.0,
            poll_interval_seconds=0.05,
            include_last_run=True,
        )
    )

    assert result["success"] is True
    assert result["reached_idle"] is True
    assert result["state"] == "IDLE"
    assert result["reason"] == ""
    # No sleeps should have happened — first poll already idle.
    assert result["elapsed_seconds"] < 0.05
    assert result["last_run"] == {"uid": "abc", "plan_name": "count"}


def test_wait_polls_until_idle(monkeypatch):
    """Three RUNNING polls then IDLE → reached_idle, elapsed ≈ 3 × interval."""
    # Two state reads per tick (state + is_idle), so duplicate each entry.
    script = (
        [EngineState.RUNNING, EngineState.RUNNING]
        + [EngineState.RUNNING, EngineState.RUNNING]
        + [EngineState.RUNNING, EngineState.RUNNING]
        + [EngineState.IDLE, EngineState.IDLE]
    )
    _patch_engine(monkeypatch, _FakeEngine(script))
    _patch_last_run(monkeypatch, None)

    poll = 0.02
    result = asyncio.run(
        et._wait_for_idle_payload(
            timeout_seconds=5.0,
            poll_interval_seconds=poll,
            include_last_run=False,
        )
    )

    assert result["reached_idle"] is True
    assert result["state"] == "IDLE"
    assert result["reason"] == ""
    # 3 sleeps of poll seconds, plus tiny overhead. Generous upper bound
    # because slow CI can stretch asyncio.sleep noticeably.
    assert 3 * poll * 0.8 <= result["elapsed_seconds"] < 3 * poll + 0.5
    assert result["last_run"] is None


def test_wait_times_out_when_engine_never_idles(monkeypatch):
    """Engine stays RUNNING → reached_idle=False, reason=='timeout'."""
    _patch_engine(monkeypatch, _FakeEngine([EngineState.RUNNING]))
    _patch_last_run(monkeypatch, None)

    timeout = 0.1
    result = asyncio.run(
        et._wait_for_idle_payload(
            timeout_seconds=timeout,
            poll_interval_seconds=0.02,
            include_last_run=True,
        )
    )

    assert result["reached_idle"] is False
    assert result["reason"] == "timeout"
    assert result["state"] == "RUNNING"
    # Elapsed should be in the vicinity of the timeout, not way over.
    assert timeout * 0.8 <= result["elapsed_seconds"] < timeout + 0.5
    # When we didn't reach idle, don't bother fetching last_run.
    assert result["last_run"] is None


def test_wait_includes_last_run_when_requested(monkeypatch):
    """include_last_run=True embeds the last-run payload on success."""
    _patch_engine(monkeypatch, _FakeEngine([EngineState.IDLE]))
    payload = {"uid": "deadbeef", "plan_name": "scan", "exit_status": "success"}
    _patch_last_run(monkeypatch, payload)

    result = asyncio.run(
        et._wait_for_idle_payload(
            timeout_seconds=1.0,
            poll_interval_seconds=0.05,
            include_last_run=True,
        )
    )

    assert result["reached_idle"] is True
    assert result["last_run"] == payload


def test_wait_omits_last_run_when_not_requested(monkeypatch):
    _patch_engine(monkeypatch, _FakeEngine([EngineState.IDLE]))
    # Even if the helper would return data, include_last_run=False suppresses it.
    _patch_last_run(monkeypatch, {"uid": "x"})

    result = asyncio.run(
        et._wait_for_idle_payload(
            timeout_seconds=1.0,
            poll_interval_seconds=0.05,
            include_last_run=False,
        )
    )

    assert result["reached_idle"] is True
    assert result["last_run"] is None


def test_wait_clamps_excessive_timeout(monkeypatch):
    """timeout_seconds > 3600 is clamped to 3600 so a typo can't hang the agent.

    We don't actually wait an hour — we hit IDLE on the first poll and verify
    the helper accepted the value without raising.
    """
    _patch_engine(monkeypatch, _FakeEngine([EngineState.IDLE]))
    _patch_last_run(monkeypatch, None)

    result = asyncio.run(
        et._wait_for_idle_payload(
            timeout_seconds=99999.0,
            poll_interval_seconds=0.05,
            include_last_run=False,
        )
    )
    assert result["reached_idle"] is True


def test_engine_tools_registers_wait_for_idle():
    """The tool must show up in the agent's create_tools() list under that name."""
    tools = et.EngineToolsAgent().create_tools()
    if not tools:
        pytest.skip("claude_agent_sdk not available")
    names = {getattr(t, "name", None) or getattr(t, "__name__", None) for t in tools}
    assert "ncs_wait_for_idle" in names


# ---------------------------------------------------------------------------
# Issue 7: distinguish "engine returned to idle with a fresh run" from
# "engine returned to idle but the plan failed before bps.open_run". The
# old code reported reached_idle=True with whichever last_run was on Tiled,
# which made a failed submission look like a successful empty run.
# ---------------------------------------------------------------------------


def _patch_uid_sequence(monkeypatch, before: str | None, after: str | None):
    """Stub `_last_run_payload` so the snapshot before the wait sees `before`
    and the snapshot after the wait sees `after`.

    Implemented as a 2-call sequence — the helper is called twice during a
    successful wait (once before, once after).
    """
    calls = {"n": 0}

    def fake_payload():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"uid": before} if before else None
        return {"uid": after} if after else None

    monkeypatch.setattr(et, "_last_run_payload", fake_payload, raising=True)


def test_plan_never_started_when_uid_unchanged_after_wait(monkeypatch):
    """Wait actually slept (engine RUNNING -> IDLE) but the last uid is
    unchanged even after the Tiled-indexing retry: the plan never opened
    a run. status must reflect that and last_run must be None so the
    agent doesn't fit stale data."""
    # Shorten the retry sleep so the test isn't gated on real-time waits.
    monkeypatch.setattr(et, "_TILED_INDEX_RETRY_S", 0.01, raising=True)
    script = (
        [EngineState.RUNNING, EngineState.RUNNING]
        + [EngineState.RUNNING, EngineState.RUNNING]
        + [EngineState.RUNNING, EngineState.RUNNING]
        + [EngineState.IDLE, EngineState.IDLE]
    )
    _patch_engine(monkeypatch, _FakeEngine(script))
    _patch_uid_sequence(monkeypatch, before="old-uid", after="old-uid")

    result = asyncio.run(
        et._wait_for_idle_payload(
            timeout_seconds=5.0,
            poll_interval_seconds=0.02,
            include_last_run=True,
        )
    )

    assert result["reached_idle"] is True
    assert result["status"] == "plan_never_started"
    # Stale run is intentionally suppressed.
    assert result["last_run"] is None
    assert result["reason"] == ""


def test_idle_when_tiled_indexes_after_engine_returns(monkeypatch):
    """Race recovery: the engine flipped back to IDLE just before Tiled
    finished indexing the new run. The first post-wait uid check sees no
    change, but after the retry sleep Tiled has the new uid — we must
    return status='idle' with the new run, NOT a false-positive
    plan_never_started. This was the symptom for "the first scan thinks
    it didn't succeed even though it did".
    """
    monkeypatch.setattr(et, "_TILED_INDEX_RETRY_S", 0.01, raising=True)
    script = (
        [EngineState.RUNNING, EngineState.RUNNING]
        + [EngineState.RUNNING, EngineState.RUNNING]
        + [EngineState.IDLE, EngineState.IDLE]
    )
    _patch_engine(monkeypatch, _FakeEngine(script))

    calls = {"n": 0}

    def fake_payload():
        calls["n"] += 1
        # Call 1: initial snapshot — old uid.
        # Call 2: immediate after-wait check — Tiled hasn't indexed yet.
        # Call 3: post-retry check — new run is visible.
        # Call 4: final last_run fetch — new run.
        if calls["n"] <= 2:
            return {"uid": "old-uid", "plan_name": "previous"}
        return {"uid": "new-uid", "plan_name": "scan", "exit_status": "success"}

    monkeypatch.setattr(et, "_last_run_payload", fake_payload, raising=True)

    result = asyncio.run(
        et._wait_for_idle_payload(
            timeout_seconds=5.0,
            poll_interval_seconds=0.02,
            include_last_run=True,
        )
    )

    assert result["status"] == "idle"
    assert result["last_run"] is not None
    assert result["last_run"]["uid"] == "new-uid"
    # The retry must have happened, otherwise the bug would still be live.
    assert calls["n"] >= 3, (
        f"retry never fired — Tiled-indexing race would still be a false "
        f"positive (_last_run_payload was called {calls['n']} times)"
    )


def test_idle_status_when_new_uid_appears(monkeypatch):
    """The good case: a new run opened during the wait → status 'idle' and
    last_run contains the new run's metadata."""
    script = (
        [EngineState.RUNNING, EngineState.RUNNING]
        + [EngineState.IDLE, EngineState.IDLE]
    )
    _patch_engine(monkeypatch, _FakeEngine(script))
    calls = {"n": 0}

    # Sequence: before-snapshot, after-snapshot, then the final last_run
    # fetch (when status='idle' the helper makes one more call to populate
    # the response payload).
    def fake_payload():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"uid": "old-uid", "plan_name": "previous"}
        return {"uid": "new-uid", "plan_name": "scan", "exit_status": "success"}

    monkeypatch.setattr(et, "_last_run_payload", fake_payload, raising=True)

    result = asyncio.run(
        et._wait_for_idle_payload(
            timeout_seconds=5.0,
            poll_interval_seconds=0.02,
            include_last_run=True,
        )
    )

    assert result["status"] == "idle"
    assert result["last_run"] == {
        "uid": "new-uid",
        "plan_name": "scan",
        "exit_status": "success",
    }


def test_already_idle_treated_as_idle_status(monkeypatch):
    """If the engine was idle on the first poll the elapsed time is below
    the grace window — we can't distinguish 'plan finished before we asked'
    from 'no plan was ever submitted', so we default to 'idle' and return
    whatever last_run Tiled has."""
    _patch_engine(monkeypatch, _FakeEngine([EngineState.IDLE]))
    _patch_uid_sequence(monkeypatch, before="some-uid", after="some-uid")

    result = asyncio.run(
        et._wait_for_idle_payload(
            timeout_seconds=1.0,
            poll_interval_seconds=0.05,
            include_last_run=True,
        )
    )

    assert result["status"] == "idle"
    assert result["last_run"] is not None
    assert result["last_run"]["uid"] == "some-uid"


def test_timeout_status_set_on_timeout(monkeypatch):
    """status='timeout' must be set when reached_idle is False; last_run is
    None regardless of include_last_run."""
    _patch_engine(monkeypatch, _FakeEngine([EngineState.RUNNING]))
    _patch_last_run(monkeypatch, None)

    result = asyncio.run(
        et._wait_for_idle_payload(
            timeout_seconds=0.05,
            poll_interval_seconds=0.02,
            include_last_run=True,
        )
    )

    assert result["reached_idle"] is False
    assert result["status"] == "timeout"
    assert result["last_run"] is None
