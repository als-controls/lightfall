"""RemoteControlService — action semantics for the remote-control contract (v1).

Thin adapters over the existing engine, plan registry, and DeviceCatalog.
Trust enforcement is NOT here — it is central in IPCService (capability
channels); every action below registers with ``trusted=True`` and is only
reachable through a session channel.

Threading: handlers register with ``main_thread=False`` (they run on the NATS
loop thread) and immediately dispatch to a small ThreadPoolExecutor, replying
from the worker — the NATS loop and the Qt main thread are never blocked.
Engine Qt signals arrive on the main thread (queued connections), so executor
threads may wait on events set from the main thread.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger
from PySide6.QtCore import QObject

from lightfall.ipc.service import IPCService
from lightfall.remote.protocol import error_reply, ok_reply

__all__ = ["RemoteControlService"]

# How long plan.run waits for the start document before replying run_uid=null.
RUN_UID_WAIT_S = 2.0
# Default completion timeout for device.put wait=true.
PUT_DEFAULT_TIMEOUT_S = 30.0


class RemoteControlService(QObject):
    """Remote-control actions + run-lifecycle events over IPC."""

    def __init__(
        self,
        ipc: IPCService,
        engine: Any = None,
        catalog: Any = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ipc = ipc
        self._engine = engine
        self._catalog = catalog
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="remote-control")
        # Current-run tracking (written on the Qt main thread by doc signals,
        # read from executor threads) — guarded by _run_lock.
        self._run_lock = threading.Lock()
        self._current: dict[str, str] = {}
        self._run_uid_waiters: dict[str, threading.Event] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def engine(self) -> Any:
        if self._engine is None:
            from lightfall.acquire.engine import get_engine

            self._engine = get_engine()
        return self._engine

    @property
    def catalog(self) -> Any:
        if self._catalog is None:
            from lightfall.devices.catalog import DeviceCatalog

            self._catalog = DeviceCatalog.get_instance()
        return self._catalog

    def start(self) -> None:
        """Register actions and events; connect engine document signals."""
        self._connect_engine_signals()
        self._register_events()
        self._register_actions()
        logger.debug("RemoteControlService started")

    def stop(self) -> None:
        """Shut down the executor (does not unregister actions)."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------
    # Engine signal wiring + broadcast events
    # ------------------------------------------------------------------

    def _connect_engine_signals(self) -> None:
        engine = self.engine
        engine.sigOutput.connect(self._on_output)
        engine.sigFinish.connect(lambda: self._publish_complete("success"))
        engine.sigAbort.connect(lambda: self._publish_complete("abort"))
        engine.sigException.connect(lambda exc: self._publish_complete("error"))
        engine.sigStateChanged.connect(self._on_state_changed)

    def _on_output(self, name: str, doc: dict) -> None:
        if name != "start":
            return
        run_uid = doc.get("uid", "")
        item = self.engine.get_current_procedure()
        item_id = getattr(item, "id", "") if item is not None else ""
        plan_name = doc.get("plan_name", getattr(item, "name", "") or "unknown")
        with self._run_lock:
            self._current = {"item_id": item_id, "run_uid": run_uid, "plan_name": plan_name}
            waiter = self._run_uid_waiters.get(item_id)
        if waiter is not None:
            waiter.set()
        self._ipc.publish(
            self._ipc.topic("runs.new"),
            {"item_id": item_id, "run_uid": run_uid, "plan_name": plan_name},
        )

    def _publish_complete(self, exit_status: str) -> None:
        with self._run_lock:
            run_uid = self._current.get("run_uid", "")
        self._ipc.publish(
            self._ipc.topic("runs.complete"),
            {"run_uid": run_uid, "exit_status": exit_status},
        )

    def _on_state_changed(self, state: str) -> None:
        self._ipc.publish(self._ipc.topic("state.engine"), {"state": state})

    def _register_events(self) -> None:
        self._ipc.register_event(
            "runs.new",
            description="Fired when a new run starts",
            schema={"item_id": "str", "run_uid": "str", "plan_name": "str"},
        )
        self._ipc.register_event(
            "runs.complete",
            description="Fired when a run finishes",
            schema={"run_uid": "str", "exit_status": "str"},
        )
        self._ipc.register_event(
            "state.engine",
            description="Engine state change",
            schema={"state": "str"},
        )

    # ------------------------------------------------------------------
    # Action registration
    # ------------------------------------------------------------------

    def _register_actions(self) -> None:
        for suffix, handler, description in [
            ("commands.engine.status", self._handle_engine_status, "Engine state + current run"),
            ("commands.queue.get", self._handle_queue_get, "List queued plan items"),
        ]:
            self._ipc.register_action(
                suffix, handler, description=description, main_thread=False, trusted=True
            )

    def _dispatch(self, fn, subject: str, data: dict, reply: str | None) -> None:
        """Run *fn* on the executor; reply with a structured error on crash."""

        def run() -> None:
            try:
                fn(subject, data, reply)
            except Exception as exc:  # handler bug — never leave the client hanging
                logger.exception("RemoteControlService: handler for '{}' failed", subject)
                self._ipc.reply(reply, error_reply("unknown", str(exc)))

        self._executor.submit(run)

    # ------------------------------------------------------------------
    # engine.status / queue.get
    # ------------------------------------------------------------------

    def _handle_engine_status(self, subject: str, data: dict, reply: str | None) -> None:
        self._dispatch(self._do_engine_status, subject, data, reply)

    def _do_engine_status(self, subject: str, data: dict, reply: str | None) -> None:
        if self.engine.is_idle:
            self._ipc.reply(reply, ok_reply(state="idle"))
            return
        with self._run_lock:
            current = dict(self._current)
        self._ipc.reply(
            reply,
            ok_reply(
                state="running",
                item_id=current.get("item_id", ""),
                run_uid=current.get("run_uid", ""),
                plan_name=current.get("plan_name", ""),
            ),
        )

    def _handle_queue_get(self, subject: str, data: dict, reply: str | None) -> None:
        self._dispatch(self._do_queue_get, subject, data, reply)

    def _do_queue_get(self, subject: str, data: dict, reply: str | None) -> None:
        items: list[dict] = []
        current = self.engine.get_current_procedure()
        if current is not None:
            items.append({"item_id": current.id, "plan_name": current.name, "state": "running"})
        for item in self.engine.get_queue_items():
            items.append({"item_id": item.id, "plan_name": item.name, "state": "queued"})
        self._ipc.reply(reply, ok_reply(items=items))
