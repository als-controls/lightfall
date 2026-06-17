from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QTimer, Signal

from lightfall.acquire.engine.console_proxy import ConsoleREProxy


class _FakeEngine(QObject):
    sigFinish = Signal()
    sigAbort = Signal()
    sigException = Signal(Exception)

    def __init__(self, underlying):
        super().__init__()
        self.RE = underlying
        self.submitted = []

    def __call__(self, *args, **kwargs):
        # Mimic the engine: submit is non-blocking, completion is async.
        self.submitted.append((args, kwargs))
        QTimer.singleShot(0, self.sigFinish.emit)


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
    proxy(plan)  # must return only after sigFinish

    assert engine.submitted == [((plan,), {})]


def test_call_reraises_engine_exception(qapp):
    re = _FakeRE()
    engine = _FakeEngine(re)

    def boom(*args, **kwargs):
        QTimer.singleShot(0, lambda: engine.sigException.emit(RuntimeError("plan failed")))

    engine.__call__ = boom  # type: ignore[method-assign]
    proxy = ConsoleREProxy(engine)

    with pytest.raises(RuntimeError, match="plan failed"):
        proxy(object())
