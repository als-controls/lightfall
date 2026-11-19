"""Exactly one TiledWriter may be subscribed to the engine at a time.

Regression for a field bug (bl602esv): two Tiled connects at startup each ran
``_subscribe_writer``, which overwrote ``_writer``/``_subscription_token``
without unsubscribing the first writer. The orphan stayed subscribed, every run
was written twice, and the second start-document POST got ``409 Conflict``.

``get_engine`` and ``ThreadedTiledWriter`` are local imports inside
``_subscribe_writer``, so they are patched at their source modules (see
test_tiled_service_max_array_size.py for the reasoning).
"""
from __future__ import annotations

import itertools
from unittest.mock import MagicMock, patch

import pytest

from lightfall.services.tiled_service import TiledAuthMode, TiledService


class _FakeEngine:
    """Minimal engine with bluesky-style subscribe/unsubscribe tokens."""

    def __init__(self) -> None:
        self._tokens = itertools.count(1)
        self.subscribers: dict[int, object] = {}

    def subscribe(self, cb: object) -> int:
        token = next(self._tokens)
        self.subscribers[token] = cb
        return token

    def unsubscribe(self, token: int) -> None:
        self.subscribers.pop(token, None)


@pytest.fixture
def service():
    TiledService.reset()
    svc = TiledService.get_instance()
    engine = _FakeEngine()
    with (
        patch("lightfall.services.tiled_service.TiledWriter", side_effect=lambda *a, **k: MagicMock()),
        patch(
            "lightfall.services.threaded_tiled_writer.ThreadedTiledWriter",
            side_effect=lambda *a, **k: MagicMock(name="threaded_writer"),
        ),
        patch("lightfall.acquire.get_engine", return_value=engine),
    ):
        svc._test_engine = engine
        yield svc
    svc._health_timer.stop()
    svc._writer = None
    svc._subscription_token = None
    svc._client = None
    TiledService.reset()


def test_repeated_subscribe_leaves_one_writer(service):
    engine = service._test_engine
    service._client = MagicMock(name="client")

    service._subscribe_writer()
    first = service._writer
    service._subscribe_writer()

    assert len(engine.subscribers) == 1
    assert list(engine.subscribers.values()) == [service._writer]
    assert service._subscription_token in engine.subscribers
    # The replaced writer was drained and stopped, not just dropped.
    first.flush.assert_called_once()
    first.stop.assert_called_once()


def test_two_connect_completions_leave_one_writer(service):
    """Both connect paths completing (the startup double-connect) -> one writer."""
    engine = service._test_engine

    service._on_connect_complete(MagicMock(name="client_a"))
    service._on_connect_complete(MagicMock(name="client_b"))

    assert len(engine.subscribers) == 1
    assert service._subscription_token in engine.subscribers


def test_stale_async_connect_result_is_ignored(service):
    """A connect superseded by a reconfigure must not install its client/writer."""
    engine = service._test_engine
    service._config = service._config.__class__(
        url="http://tiled.example/", api_key=None, enabled=True, auth_mode=TiledAuthMode.NONE
    )

    with patch("lightfall.services.tiled_service.QThreadFuture") as qtf:
        service.connect_async()  # generation 1, in flight
        stale_gen = qtf.call_args.args[1]
        # configure() with a different URL (e.g. trailing slash) disconnects
        # and starts a new connect while the first is still running.
        service.configure(url="http://tiled.example", enabled=True)
        service.connect_async()
        fresh_gen = qtf.call_args.args[1]

    assert fresh_gen != stale_gen
    fresh_client = MagicMock(name="fresh")
    service._on_connect_result((fresh_gen, fresh_client))
    service._on_connect_result((stale_gen, MagicMock(name="stale")))

    assert service.client is fresh_client
    assert len(engine.subscribers) == 1


def test_stale_async_connect_error_is_ignored(service):
    service._on_connect_complete(MagicMock(name="client"))
    service._connect_generation = 5
    err = RuntimeError("boom")
    err._lightfall_tiled_connect_generation = 4

    with patch.object(service, "_on_connect_error") as on_err:
        service._on_connect_failure(err)
    on_err.assert_not_called()

    err._lightfall_tiled_connect_generation = 5
    with patch.object(service, "_on_connect_error") as on_err:
        service._on_connect_failure(err)
    on_err.assert_called_once_with(err)
