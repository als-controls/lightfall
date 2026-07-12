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

import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger
from PySide6.QtCore import QObject

from lightfall.ipc.service import IPCService
from lightfall.remote.protocol import error_reply, ok_reply

try:
    from ophyd.utils import WaitTimeoutError
except ImportError:  # pragma: no cover - defensive; installed ophyd always has it
    WaitTimeoutError = None

__all__ = ["RemoteControlService"]

# How long plan.run waits for the start document before replying run_uid=null.
RUN_UID_WAIT_S = 2.0
# Default completion timeout for device.put wait=true.
PUT_DEFAULT_TIMEOUT_S = 30.0


def _get_plan_registry():
    """Indirection point so tests can monkeypatch the registry lookup."""
    from lightfall.acquire.plans.registry import get_registry

    return get_registry()


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
            ("commands.plan.list", self._handle_plan_list, "List available plans with parameter metadata"),
            ("commands.plan.run", self._handle_plan_run, "Submit a plan (behavior: reject|queue, default reject)"),
            ("commands.plan.abort", self._handle_plan_abort, "Abort the active run"),
            ("commands.device.search", self._handle_device_search, "Search devices (happi-style filters)"),
            (
                "commands.device.components",
                self._handle_device_components,
                "List a device's sub-devices and signals",
            ),
            ("commands.device.info", self._handle_device_info, "Thin device metadata"),
            ("commands.device.get", self._handle_device_get, "Read a device signal value"),
            (
                "commands.device.put",
                self._handle_device_put,
                "Write a device signal (ca put-callback semantics)",
            ),
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

    # ------------------------------------------------------------------
    # plan.list / plan.run / plan.abort
    # ------------------------------------------------------------------

    def _handle_plan_list(self, subject: str, data: dict, reply: str | None) -> None:
        self._dispatch(self._do_plan_list, subject, data, reply)

    def _do_plan_list(self, subject: str, data: dict, reply: str | None) -> None:
        from lightfall.ui.annotations import Default, Unit
        from lightfall.ui.widgets.plan_config import extract_annotated_metadata

        registry = _get_plan_registry()
        plans = []
        for info in registry.list_plans():
            params = []
            for p in info.parameters:
                try:
                    base_type, metadata = extract_annotated_metadata(p.annotation, info.func)
                except Exception:
                    base_type, metadata = p.annotation, []
                unit = next((m.suffix for m in metadata if isinstance(m, Unit)), None)
                default = None
                if p.default is not inspect.Parameter.empty:
                    default = p.default
                else:
                    default = next(
                        (m.value for m in metadata if isinstance(m, Default)), None
                    )
                type_name = getattr(base_type, "__name__", str(base_type))
                params.append({"name": p.name, "type": type_name, "unit": unit, "default": default})
            plans.append({"name": info.name, "params": params})
        self._ipc.reply(reply, ok_reply(plans=plans))

    def _handle_plan_run(self, subject: str, data: dict, reply: str | None) -> None:
        self._dispatch(self._do_plan_run, subject, data, reply)

    def _do_plan_run(self, subject: str, data: dict, reply: str | None) -> None:
        plan_name = data.get("plan_name")
        params = data.get("params", {})
        behavior = data.get("behavior", "reject")

        if not plan_name:
            self._ipc.reply(reply, error_reply("bad_request", "plan_name is required"))
            return
        if behavior not in ("reject", "queue"):
            self._ipc.reply(
                reply,
                error_reply(
                    "bad_request", f"behavior must be 'reject' or 'queue', got {behavior!r}"
                ),
            )
            return

        registry = _get_plan_registry()
        plan_info = registry.get_plan(plan_name)
        if plan_info is None:
            self._ipc.reply(reply, error_reply("unknown", f"Plan '{plan_name}' not found"))
            return

        engine = self.engine
        busy = not engine.is_idle or getattr(engine, "queue_size", 0) > 0
        if behavior == "reject" and busy:
            self._ipc.reply(reply, error_reply("busy", "Engine is busy and behavior is 'reject'"))
            return

        try:
            plan_generator = plan_info.func(**params)
        except TypeError as exc:
            self._ipc.reply(reply, error_reply("bad_request", f"Bad params: {exc}"))
            return

        # Arm the start-doc waiter BEFORE submitting so a fast start can't race us.
        waiter = threading.Event()
        try:
            item_id = engine.submit(plan_generator, name=plan_name)
        except Exception as exc:
            self._ipc.reply(reply, error_reply("unknown", str(exc)))
            return
        if item_id is None:
            self._ipc.reply(reply, error_reply("unknown", "Submission cancelled by pre-submit hook"))
            return

        run_uid: str | None = None
        if not busy:
            # The waiter is registered AFTER submit (item_id is only known once
            # submit() returns), so the start doc can race ahead of registration
            # for a very fast plan. Guard against that race by checking
            # self._current immediately after registering, before waiting.
            with self._run_lock:
                self._run_uid_waiters[item_id] = waiter
                if self._current.get("item_id") == item_id:
                    run_uid = self._current.get("run_uid")
            try:
                if run_uid is None and waiter.wait(RUN_UID_WAIT_S):
                    with self._run_lock:
                        if self._current.get("item_id") == item_id:
                            run_uid = self._current.get("run_uid")
            finally:
                with self._run_lock:
                    self._run_uid_waiters.pop(item_id, None)
            # Start doc may have arrived just as the waiter was being torn down.
            if run_uid is None:
                with self._run_lock:
                    if self._current.get("item_id") == item_id:
                        run_uid = self._current.get("run_uid")

        self._ipc.reply(
            reply,
            ok_reply(status="submitted", plan_name=plan_name, item_id=item_id, run_uid=run_uid),
        )

    def _handle_plan_abort(self, subject: str, data: dict, reply: str | None) -> None:
        """Abort marshals to the Qt main thread (matches how the UI calls it).

        Request may include a selector (spec: ``{item_id? | run_uid?, reason?}``):

        - No selector: abort the active run (previous behavior).
        - ``item_id``: abort if it names the current run; remove it from the
          queue if it's a queued item; else ``not_aborted``.
        - ``run_uid``: abort if it names the tracked current run; else
          ``not_aborted``.
        - Both given: ``bad_request``.
        """
        from lightfall.utils.threads import invoke_in_main_thread

        reason = data.get("reason", "")
        item_id = data.get("item_id")
        run_uid = data.get("run_uid")

        if item_id and run_uid:
            self._ipc.reply(
                reply,
                error_reply("bad_request", "Provide at most one of item_id or run_uid, not both"),
            )
            return

        def _abort_engine() -> None:
            try:
                aborted = self.engine.abort(reason=reason)
            except Exception as exc:
                self._ipc.reply(reply, error_reply("unknown", str(exc)))
                return
            if aborted:
                self._ipc.reply(reply, ok_reply(status="abort_requested"))
            else:
                self._ipc.reply(
                    reply,
                    ok_reply(
                        status="not_aborted",
                        message=f"Nothing to abort: engine state is '{self.engine.state_name}'",
                    ),
                )

        def do_abort() -> None:
            if item_id:
                with self._run_lock:
                    current_item_id = self._current.get("item_id")
                if item_id == current_item_id:
                    _abort_engine()
                    return
                try:
                    removed = self.engine.remove_from_queue(item_id)
                except Exception as exc:
                    self._ipc.reply(reply, error_reply("unknown", str(exc)))
                    return
                if removed:
                    self._ipc.reply(reply, ok_reply(status="abort_requested"))
                else:
                    self._ipc.reply(
                        reply,
                        ok_reply(
                            status="not_aborted",
                            message=f"item_id '{item_id}' is not the current run or in the queue",
                        ),
                    )
                return

            if run_uid:
                with self._run_lock:
                    current_run_uid = self._current.get("run_uid")
                if run_uid == current_run_uid:
                    _abort_engine()
                else:
                    self._ipc.reply(
                        reply,
                        ok_reply(
                            status="not_aborted",
                            message=f"run_uid '{run_uid}' is not the current run",
                        ),
                    )
                return

            _abort_engine()

        invoke_in_main_thread(do_abort)

    # ------------------------------------------------------------------
    # device.* helpers
    # ------------------------------------------------------------------

    def _resolve_device(
        self, data: dict, reply: str | None
    ) -> tuple[Any, Any] | None:
        """Common device lookup; replies with an error and returns None on failure.

        Returns (DeviceInfo, ophyd_obj) on success. ophyd_obj may be None if
        the device is catalogued but not instantiated.
        """
        name = data.get("device")
        if not name:
            self._ipc.reply(reply, error_reply("bad_request", "device is required"))
            return None
        info = self.catalog.get_device_by_name(name)
        if info is None:
            self._ipc.reply(reply, error_reply("unknown", f"Device '{name}' not found"))
            return None
        return info, self.catalog.get_ophyd_device(name)

    @staticmethod
    def _is_writable(obj: Any) -> bool:
        """Mirror of the signal_control heuristic (see signal_control.py:77)."""
        cls_name = type(obj).__name__
        if "ReadOnly" in cls_name or "RO" in cls_name:
            return False
        return hasattr(obj, "put") or hasattr(obj, "set")

    def _handle_device_search(self, subject: str, data: dict, reply: str | None) -> None:
        self._dispatch(self._do_device_search, subject, data, reply)

    def _do_device_search(self, subject: str, data: dict, reply: str | None) -> None:
        filters = {k: v for k, v in data.items() if k not in ("_identity", "contract_version")}
        names: list[str] = []
        for info in self.catalog.list_devices():
            if self._matches(info, filters):
                names.append(info.name)
        self._ipc.reply(reply, ok_reply(devices=sorted(names)))

    @staticmethod
    def _matches(info: Any, filters: dict) -> bool:
        for key, wanted in filters.items():
            actual = getattr(info, key, None)
            if actual is None:
                actual = (info.metadata or {}).get(key)
            if actual is None:
                return False
            if isinstance(actual, (list, set, tuple)):
                if wanted not in actual:
                    return False
            elif str(actual) != str(wanted):
                return False
        return True

    def _handle_device_info(self, subject: str, data: dict, reply: str | None) -> None:
        self._dispatch(self._do_device_info, subject, data, reply)

    def _do_device_info(self, subject: str, data: dict, reply: str | None) -> None:
        resolved = self._resolve_device(data, reply)
        if resolved is None:
            return
        info, _ = resolved
        self._ipc.reply(
            reply,
            ok_reply(name=info.name, category=str(info.category), device_class=info.device_class),
        )

    def _handle_device_components(self, subject: str, data: dict, reply: str | None) -> None:
        self._dispatch(self._do_device_components, subject, data, reply)

    def _do_device_components(self, subject: str, data: dict, reply: str | None) -> None:
        resolved = self._resolve_device(data, reply)
        if resolved is None:
            return
        info, obj = resolved
        if obj is None:
            self._ipc.reply(
                reply, error_reply("unknown", f"Device '{info.name}' is not instantiated")
            )
            return

        components: list[dict] = []
        # Lazy-safe enumeration: use the instantiated-signal dict and class
        # attrs rather than getattr, which would trigger lazy Components
        # (same approach as ui/models/device_tree.py).
        names = list(getattr(obj, "component_names", ()) or ())
        signals = getattr(obj, "_signals", {}) or {}
        for cname in names:
            comp = signals.get(cname)
            if comp is None:
                sig_attrs = getattr(obj, "_sig_attrs", {}) or {}
                cpt = sig_attrs.get(cname)
                cls = getattr(cpt, "cls", None)
                type_name = cls.__name__ if cls is not None else "unknown"
                writable = bool(
                    cls is not None
                    and not ("ReadOnly" in cls.__name__ or "RO" in cls.__name__)
                    and (hasattr(cls, "put") or hasattr(cls, "set"))
                )
            else:
                type_name = type(comp).__name__
                writable = self._is_writable(comp)
            components.append({"name": cname, "type": type_name, "writable": writable})
        self._ipc.reply(reply, ok_reply(components=components))

    # ------------------------------------------------------------------
    # device.get / device.put
    # ------------------------------------------------------------------

    def _resolve_signal(self, obj: Any, signal_name: str | None) -> Any | None:
        """Resolve a signal on *obj*.

        ``None`` -> the device's primary readback: ``user_readback`` then
        ``readback`` (motor conventions, see ui/widgets/motor_control.py:207),
        else the object itself when it is signal-like (has ``get``).
        Named lookup walks the instantiated ``_signals`` dict first (lazy-safe),
        supports dotted paths for nested components.
        """
        if signal_name is None:
            signals = getattr(obj, "_signals", {}) or {}
            for attr in ("user_readback", "readback"):
                sig = signals.get(attr)
                if sig is not None:
                    return sig
            return obj if hasattr(obj, "get") else None

        current = obj
        for part in signal_name.split("."):
            signals = getattr(current, "_signals", {}) or {}
            nxt = signals.get(part)
            if nxt is None:
                # Fall back to getattr ONLY for already-instantiated attrs
                nxt = current.__dict__.get(part) or getattr(type(current), part, None)
                if nxt is None or not hasattr(nxt, "get"):
                    return None
            current = nxt
        return current

    def _handle_device_get(self, subject: str, data: dict, reply: str | None) -> None:
        self._dispatch(self._do_device_get, subject, data, reply)

    def _do_device_get(self, subject: str, data: dict, reply: str | None) -> None:
        resolved = self._resolve_device(data, reply)
        if resolved is None:
            return
        info, obj = resolved
        if obj is None:
            self._ipc.reply(reply, error_reply("unknown", f"Device '{info.name}' is not instantiated"))
            return
        sig = self._resolve_signal(obj, data.get("signal"))
        if sig is None:
            self._ipc.reply(
                reply,
                error_reply("unknown", f"Signal '{data.get('signal')}' not found on '{info.name}'"),
            )
            return
        try:
            reading = sig.read()
            key = next(iter(reading))
            value = reading[key]["value"]
            timestamp = reading[key]["timestamp"]
        except Exception:
            value = sig.get()
            timestamp = time.time()
        if hasattr(value, "tolist"):
            value = value.tolist()  # numpy scalar/array -> JSON-safe
        self._ipc.reply(reply, ok_reply(value=value, timestamp=float(timestamp)))

    def _handle_device_put(self, subject: str, data: dict, reply: str | None) -> None:
        self._dispatch(self._do_device_put, subject, data, reply)

    def _do_device_put(self, subject: str, data: dict, reply: str | None) -> None:
        resolved = self._resolve_device(data, reply)
        if resolved is None:
            return
        info, obj = resolved
        if obj is None:
            self._ipc.reply(reply, error_reply("unknown", f"Device '{info.name}' is not instantiated"))
            return

        if "value" not in data:
            self._ipc.reply(reply, error_reply("bad_request", "value is required"))
            return
        behavior = data.get("behavior", "reject")
        if behavior != "reject":
            self._ipc.reply(
                reply,
                error_reply("bad_request", "device.put supports only behavior='reject' in v1"),
            )
            return
        if not self.engine.is_idle:
            self._ipc.reply(
                reply, error_reply("busy", "Engine is not idle; puts are rejected mid-scan")
            )
            return

        sig = self._resolve_signal(obj, data.get("signal"))
        # For a put with no explicit signal on a positioner, set the DEVICE
        # (motor.set moves the motor); only fall back to the readback for get.
        if data.get("signal") is None and hasattr(obj, "set"):
            sig = obj
        if sig is None:
            self._ipc.reply(
                reply,
                error_reply("unknown", f"Signal '{data.get('signal')}' not found on '{info.name}'"),
            )
            return
        if not self._is_writable(sig):
            self._ipc.reply(reply, error_reply("limits", "Signal is read-only"))
            return

        value = data["value"]
        wait = data.get("wait", True)
        timeout_s = float(data.get("timeout_s", PUT_DEFAULT_TIMEOUT_S))

        try:
            if hasattr(sig, "set"):
                status = sig.set(value)
            else:
                sig.put(value)
                status = None
        except Exception as exc:
            code = "limits" if "limit" in type(exc).__name__.lower() or "limit" in str(exc).lower() else "unknown"
            self._ipc.reply(reply, error_reply(code, str(exc)))
            return

        if not wait:
            self._ipc.reply(reply, ok_reply(status="accepted"))
            return

        if status is not None and hasattr(status, "wait"):
            try:
                status.wait(timeout=timeout_s)
            except Exception as exc:
                is_timeout = (WaitTimeoutError is not None and isinstance(exc, WaitTimeoutError)) or (
                    "timeout" in type(exc).__name__.lower()
                )
                if is_timeout:
                    self._ipc.reply(
                        reply, error_reply("timeout", f"Put did not complete within {timeout_s}s")
                    )
                else:
                    self._ipc.reply(reply, error_reply("unknown", str(exc)))
                return
        self._ipc.reply(reply, ok_reply(status="ok", value=value))
