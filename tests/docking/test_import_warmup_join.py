"""_await_import_warmup serializes panel imports against the warmup thread.

Regression for the post-login hang on ws5: the background import-warmup thread
(importing e.g. lightfall.claude -> pygments) deadlocked on Python's per-module
import lock against a GUI-thread panel import that shared a submodule (the
IPython console panel imports pygments/traitlets/jedi). Joining the warmup
before instantiating a deferred panel makes the imports non-concurrent.

The method is pure (no Qt), so it's exercised unbound with a stub ``self``.
"""
from __future__ import annotations

from types import SimpleNamespace

from lightfall.ui.docking.manager import DockingManager


class _FakeThread:
    def __init__(self, running: bool, wait_ok: bool = True):
        self._running = running
        self._wait_ok = wait_ok
        self.waited_ms = None

    def isRunning(self):
        return self._running

    def wait(self, ms):
        self.waited_ms = ms
        return self._wait_ok


def test_await_warmup_noop_when_no_thread():
    stub = SimpleNamespace(_warmup_thread=None)
    DockingManager._await_import_warmup(stub)  # must not raise
    assert stub._warmup_thread is None


def test_await_warmup_joins_running_thread_and_clears():
    thread = _FakeThread(running=True)
    stub = SimpleNamespace(_warmup_thread=thread)

    DockingManager._await_import_warmup(stub)

    assert thread.waited_ms == 60_000      # joined with the bounded wait
    assert stub._warmup_thread is None     # handle dropped so later panels skip


def test_await_warmup_skips_wait_when_already_done():
    thread = _FakeThread(running=False)
    stub = SimpleNamespace(_warmup_thread=thread)

    DockingManager._await_import_warmup(stub)

    assert thread.waited_ms is None        # not running -> no wait
    assert stub._warmup_thread is None


def test_await_warmup_tolerates_timeout():
    thread = _FakeThread(running=True, wait_ok=False)  # wait() times out
    stub = SimpleNamespace(_warmup_thread=thread)

    DockingManager._await_import_warmup(stub)  # must not raise

    assert stub._warmup_thread is None  # still cleared; falls back to inline import
