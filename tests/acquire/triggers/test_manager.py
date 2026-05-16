"""Tests for TriggerManager — engine subscription, fire routing."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lucid.acquire.triggers.base import Trigger
from lucid.acquire.triggers.manager import TriggerManager


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


class _RecordingTrigger(Trigger):
    def __init__(self):
        self.attached_to = None
        self.detached = False
        self.fires = []

    def attach(self, manager):
        self.attached_to = manager

    def detach(self):
        self.detached = True

    def fire(self, run_uid, parameters):
        self.fires.append((run_uid, parameters))


def test_manager_attaches_triggers():
    engine = _FakeEngine()
    mgr = TriggerManager(engine=engine, submit_callable=MagicMock())
    t = _RecordingTrigger()
    mgr.add(t)
    assert t.attached_to is mgr


def test_manager_detaches_on_remove():
    engine = _FakeEngine()
    mgr = TriggerManager(engine=engine, submit_callable=MagicMock())
    t = _RecordingTrigger()
    mgr.add(t)
    mgr.remove(t)
    assert t.detached


def test_manager_routes_fire_to_submit_callable():
    engine = _FakeEngine()
    submit = MagicMock()
    mgr = TriggerManager(engine=engine, submit_callable=submit)
    mgr.fire(pipeline="reduce_saxs", run_uid="abc", parameters={"k": 1})
    submit.assert_called_once_with(pipeline="reduce_saxs", run_uid="abc", parameters={"k": 1})


def test_manager_exposes_engine_subscribe_to_subclasses():
    engine = _FakeEngine()
    mgr = TriggerManager(engine=engine, submit_callable=MagicMock())
    called = []
    tok = mgr.subscribe_engine(lambda name, doc: called.append((name, doc)))
    engine.emit("start", {"uid": "u1"})
    assert called == [("start", {"uid": "u1"})]
    mgr.unsubscribe_engine(tok)
    engine.emit("start", {"uid": "u2"})
    assert called == [("start", {"uid": "u1"})]
