"""Tests for RunEndTrigger."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lucid.acquire.triggers.filter import FilterPredicate
from lucid.acquire.triggers.manager import TriggerManager
from lucid.acquire.triggers.run_end import RunEndTrigger


class _FakeEngine:
    def __init__(self):
        self._cbs = {}
        self._next = 1

    def subscribe(self, cb):
        tok = self._next
        self._next += 1
        self._cbs[tok] = cb
        return tok

    def unsubscribe(self, tok):
        self._cbs.pop(tok, None)

    def emit(self, name, doc):
        for cb in list(self._cbs.values()):
            cb(name, doc)


def test_run_end_fires_on_stop_when_paired_start_matches():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.add(RunEndTrigger(
        filter=FilterPredicate(plan_name="count"),
        pipeline="reduce_saxs",
        parameter_overrides={},
    ))

    # 1) start doc, cached internally by the trigger
    engine.emit("start", {"uid": "abc", "plan_name": "count", "tags": ["saxs"]})
    # 2) stop doc refers back to abc; the trigger pulls the cached start to filter
    engine.emit("stop", {"uid": "stop1", "run_start": "abc"})

    submit.assert_called_once_with(pipeline="reduce_saxs", run_uid="abc", parameters={})


def test_run_end_ignores_stop_without_matching_start():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.add(RunEndTrigger(
        filter=FilterPredicate(plan_name="count"),
        pipeline="p",
        parameter_overrides={},
    ))

    engine.emit("start", {"uid": "abc", "plan_name": "scan", "tags": []})
    engine.emit("stop", {"uid": "s", "run_start": "abc"})

    submit.assert_not_called()


def test_run_end_handles_stop_with_unknown_start():
    """Stop arriving before/without its start doc is silently ignored."""
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.add(RunEndTrigger(
        filter=FilterPredicate(),
        pipeline="p",
        parameter_overrides={},
    ))

    engine.emit("stop", {"uid": "s", "run_start": "never-seen"})

    submit.assert_not_called()
