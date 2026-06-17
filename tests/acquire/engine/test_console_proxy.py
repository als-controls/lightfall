from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QTimer, Signal

from lightfall.acquire.engine.console_proxy import ConsoleREProxy


class _FakeEngine(QObject):
    """Engine double: submit returns a per-call id and schedules a matching
    sigProcedureFinished(id, error) on the event loop."""

    sigProcedureFinished = Signal(str, object)

    def __init__(self, underlying, *, error=None):
        super().__init__()
        self.RE = underlying
        self.submitted = []
        self._error = error
        self._counter = 0

    def __call__(self, *args, **kwargs):
        self._counter += 1
        pid = f"proc-{self._counter}"
        self.submitted.append((pid, args, kwargs))
        err = self._error
        QTimer.singleShot(0, lambda: self.sigProcedureFinished.emit(pid, err))
        return pid


class _FakeRE:
    def __init__(self):
        self.md = {"scan_id": 1}
        self.subscribed = []

    def subscribe(self, cb):
        self.subscribed.append(cb)
        return 7


def test_attribute_get_and_set_delegate_to_underlying_re(qapp):
    re = _FakeRE()
    proxy = ConsoleREProxy(_FakeEngine(re))

    # get delegates
    assert proxy.md == {"scan_id": 1}
    assert proxy.subscribe(lambda n, d: None) == 7
    # set delegates (proposal_swap does RE.md = ...)
    proxy.md = {"scan_id": 99, "data_session": "pass-1"}
    assert re.md == {"scan_id": 99, "data_session": "pass-1"}


def test_call_submits_and_blocks_until_finish(qapp):
    engine = _FakeEngine(_FakeRE())
    proxy = ConsoleREProxy(engine)

    def fake_plan():
        yield None

    plan = fake_plan()
    proxy(plan)  # must return only after this procedure's completion

    assert len(engine.submitted) == 1
    assert engine.submitted[0][1] == (plan,)


def test_call_reraises_engine_exception(qapp):
    engine = _FakeEngine(_FakeRE(), error=RuntimeError("plan failed"))
    proxy = ConsoleREProxy(engine)

    with pytest.raises(RuntimeError, match="plan failed"):
        proxy(object())


def test_call_returns_immediately_when_submit_cancelled(qapp):
    """If submit returns None (pre-submit hook cancelled), the call must not
    block waiting for a completion that will never come."""
    engine = _FakeEngine(_FakeRE())
    engine.__call__ = lambda *a, **k: None  # type: ignore[method-assign]
    proxy = ConsoleREProxy(engine)

    proxy(object())  # returns without hanging


def test_call_blocks_until_matching_procedure_id(qapp):
    """A completion for a DIFFERENT procedure must not release this call; the
    call returns only when its own procedure id completes."""
    engine = _FakeEngine(_FakeRE())
    proxy = ConsoleREProxy(engine)
    observed = {"foreign_seen_while_blocked": False}

    def _call(*args, **kwargs):
        engine._counter += 1
        pid = f"proc-{engine._counter}"
        engine.submitted.append((pid, args, kwargs))
        # t=0: a foreign completion the proxy must ignore (stay blocked).
        QTimer.singleShot(0, lambda: engine.sigProcedureFinished.emit("FOREIGN", None))

        # t=30ms: reached ONLY because the proxy stayed in its event loop past
        # the foreign completion. Mark it, then emit the matching id to release.
        def _matching():
            observed["foreign_seen_while_blocked"] = True
            engine.sigProcedureFinished.emit(pid, None)

        QTimer.singleShot(30, _matching)
        return pid

    engine.__call__ = _call  # type: ignore[method-assign]

    proxy(object())

    # If the proxy had wrongly returned on the FOREIGN completion, its event
    # loop would have exited before the 30ms timer fired and this stays False.
    assert observed["foreign_seen_while_blocked"] is True
