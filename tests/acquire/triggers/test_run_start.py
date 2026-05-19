"""Tests for RunStartTrigger."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lucid.acquire.triggers.filter import FilterPredicate
from lucid.acquire.triggers.manager import TriggerManager
from lucid.acquire.triggers.run_start import RunStartTrigger


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


def test_run_start_fires_on_matching_start_doc():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    trigger = RunStartTrigger(
        filter=FilterPredicate(plan_name="count"),
        pipeline="reduce_saxs",
        parameter_overrides={"roi_x": [0, 1024]},
    )
    mgr.add(trigger)

    engine.emit("start", {"uid": "abc", "plan_name": "count", "tags": ["saxs"]})

    submit.assert_called_once_with(
        pipeline="reduce_saxs",
        run_uid="abc",
        parameters={"roi_x": [0, 1024]},
        input_access_blob={},
    )


def test_run_start_forwards_access_blob_from_start_doc():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.add(RunStartTrigger(
        filter=FilterPredicate(plan_name="count"),
        pipeline="p",
        parameter_overrides={},
    ))
    blob = {"esaf_id": "BLS-00480-001"}
    engine.emit("start", {"uid": "abc", "plan_name": "count", "access_blob": blob})

    submit.assert_called_once_with(
        pipeline="p",
        run_uid="abc",
        parameters={},
        input_access_blob=blob,
    )


def test_run_start_ignores_non_matching():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.add(RunStartTrigger(
        filter=FilterPredicate(plan_name="count"),
        pipeline="reduce_saxs",
        parameter_overrides={},
    ))

    engine.emit("start", {"uid": "abc", "plan_name": "scan", "tags": []})

    submit.assert_not_called()


def test_run_start_ignores_non_start_docs():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.add(RunStartTrigger(
        filter=FilterPredicate(),  # match-all
        pipeline="p",
        parameter_overrides={},
    ))

    engine.emit("stop", {"uid": "abc", "run_start": "xyz"})
    engine.emit("descriptor", {"uid": "abc"})

    submit.assert_not_called()


def test_run_start_detach_unsubscribes():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    trigger = RunStartTrigger(
        filter=FilterPredicate(),
        pipeline="p",
        parameter_overrides={},
    )
    mgr.add(trigger)
    mgr.remove(trigger)

    engine.emit("start", {"uid": "x", "plan_name": "count"})

    submit.assert_not_called()
