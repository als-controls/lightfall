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
        self._exception_slot: Callable | None = None  # lambda stored for disconnect
        self._timer = QTimer(self)
        self._timer.setInterval(int(tick_granularity_s * 1000))
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if self._token is None:
            self._token = self._engine.subscribe(self._on_document)
            # Connect abort/exception signals exactly once per start/stop cycle.
            # Store the lambda so we can disconnect it symmetrically in stop().
            self._exception_slot = lambda _e: self._disarm()
            self._engine.sigAbort.connect(self._disarm)
            self._engine.sigException.connect(self._exception_slot)

    def stop(self) -> None:
        if self._token is not None:
            self._engine.unsubscribe(self._token)
            self._token = None
            # Disconnect the signals that were connected in start().
            self._engine.sigAbort.disconnect(self._disarm)
            if self._exception_slot is not None:
                self._engine.sigException.disconnect(self._exception_slot)
                self._exception_slot = None
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

    def set_tick_interval_s(self, seconds: float) -> None:
        self._timer.setInterval(int(max(1.0, seconds) * 1000))

    def _derived(self, name: str) -> dict | None:
        """Provider for DataWindow.derived(name). For "xpcs": read xpcs_live's
        latest recorded Tiled snapshot for the active run. Reduced metrics only
        (no analysis). Runs on the eval thread; degrades to None on any miss."""
        if name != "xpcs":
            return None
        uid = self._buffer.active_uid
        if not uid:
            return None
        try:
            from lightfall.services.tiled_service import TiledService
            svc = TiledService.get_instance()
            client = svc.client
            if client is None or not svc.is_connected:
                return None
            run = client[uid]                 # KeyError if writer still lagging
            xpcs = run["xpcs"]                # KeyError until first snapshot
            snaps = sorted(k for k in xpcs.keys() if k.startswith("snapshot_"))
            if not snaps:
                return None
            snap = xpcs[snaps[-1]]
        except KeyError:
            return None
        except Exception:  # noqa: BLE001 — advisory; never crash the tick
            logger.debug("monitor _derived('xpcs') read failed for {}", uid)
            return None

        def arr(k):
            try:
                return snap[k].read()
            except Exception:  # noqa: BLE001
                return None

        keys = list(snap.keys())
        g2 = {"average": arr("g2_average")}
        for k in keys:
            if k.startswith("g2_roi_"):
                g2[k[len("g2_roi_"):]] = arr(k)
        fc = arr("frames_count")
        try:
            frames_count = int(fc) if fc is not None else 0
        except (TypeError, ValueError):
            frames_count = 0
        return {
            "tau": arr("tau"),
            "g2": g2,
            "frames_count": frames_count,
            "metrics": {k: arr(k) for k in keys if k.startswith("metrics_")},
            "intensity_average": arr("intensity_average"),
            "snapshot": snaps[-1],
        }

    def _tick(self) -> None:
        if not self._active:
            return
        now = self._clock()
        window = self._buffer.snapshot(now=time.time())
        window.derived_provider = self._derived
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
