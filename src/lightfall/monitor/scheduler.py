"""Drives MonitorFeed evaluation during a run.

- Subscribes to the engine document stream (worker thread) and feeds a
  thread-safe RollingBuffer; arms on 'start' (uid from the start doc, since
  sigStart is payload-less), disarms on 'stop'/abort.
- On a QTimer (GUI thread) evaluates each due enabled feed OFF the UI thread
  via QThreadFuture, rate-limits results, and emits `observation`.
Never blocks the engine: the document callback only appends + marshals
arm/disarm to the GUI thread."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from lightfall.monitor.buffer import RollingBuffer
from lightfall.monitor.models import ExperimentContext, Observation
from lightfall.monitor.rate_limiter import RateLimiter
from lightfall.monitor.registry import MonitorRegistry
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture, invoke_in_main_thread


class MonitorScheduler(QObject):
    observation = Signal(object)  # Observation

    def __init__(
        self,
        engine: Any,
        registry: MonitorRegistry | None = None,
        tick_granularity_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        eval_async: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._registry = registry or MonitorRegistry.get_instance()
        self._clock = clock
        self._eval_async = eval_async
        self._buffer = RollingBuffer()
        self._rate = RateLimiter()
        self._prior: list[Observation] = []
        self._ctx: ExperimentContext = ExperimentContext.default()
        self._last_eval: dict[str, float] = {}
        self._active = False
        self._token: int | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(int(tick_granularity_s * 1000))
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if self._token is None:
            self._token = self._engine.subscribe(self._on_document)
        self._engine.sigAbort.connect(self._disarm)
        self._engine.sigException.connect(lambda _e: self._disarm())

    def stop(self) -> None:
        if self._token is not None:
            self._engine.unsubscribe(self._token)
            self._token = None
        self._timer.stop()

    # --- engine worker thread ---
    def _on_document(self, name: str, doc: dict[str, Any]) -> None:
        self._buffer(name, doc)
        if name == "start":
            ctx = ExperimentContext.from_start_doc(doc)
            invoke_in_main_thread(self._arm, doc.get("uid", ""), ctx)
        elif name == "stop":
            invoke_in_main_thread(self._disarm)

    # --- GUI thread ---
    def _arm(self, uid: str, ctx: ExperimentContext) -> None:
        self._ctx = ctx
        self._prior = []
        self._rate.reset()
        self._last_eval = {}
        self._active = True
        self._timer.start()

    def _disarm(self) -> None:
        self._active = False
        self._timer.stop()

    def _tick(self) -> None:
        if not self._active:
            return
        now = self._clock()
        window = self._buffer.snapshot(now=time.time())
        for feed in self._registry.enabled_feeds():
            last = self._last_eval.get(feed.name, float("-inf"))
            if now - last < feed.default_interval_s:
                continue
            self._last_eval[feed.name] = now
            self._dispatch(feed, window)

    def _dispatch(self, feed: Any, window: Any) -> None:
        prior = list(self._prior)
        if self._eval_async:
            QThreadFuture(
                self._safe_eval, feed, window, prior,
                callback_slot=self._on_observation,
                key=f"monitor:{feed.name}",
            ).start()
        else:
            self._on_observation(self._safe_eval(feed, window, prior))

    def _safe_eval(self, feed: Any, window: Any, prior: list[Observation]) -> Observation | None:
        try:
            return feed.evaluate(self._ctx, window, prior)
        except Exception:  # noqa: BLE001 — advisory; never crash the app
            logger.exception("monitor feed '{}' raised during evaluate", getattr(feed, "name", "?"))
            return None

    def _on_observation(self, obs: Observation | None) -> None:
        if obs is None:
            return
        if not obs.ts:
            obs.ts = time.time()
        if self._rate.should_surface(obs):
            self._prior.append(obs)
            self.observation.emit(obs)
