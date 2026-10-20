"""ServiceManager: owns Lightfall's out-of-process services (IPC services pattern).

Each registered :class:`~lightfall_utils.ipc.services.descriptor.ServiceDescriptor` gets
one :class:`~lightfall_utils.ipc.services.qt.QtServiceClient`:

- **Local** services (``launch="local"``) start lazily, on the first
  :meth:`ServiceManager.ensure_started`. The manager makes sure a host-local NATS broker
  answers on ``127.0.0.1:<services_local_port>`` (default 4223): if ``probe_nats`` fails it
  starts a :class:`LocalNatsServer` and owns it. The probe/start runs on a worker thread
  (the GUI never blocks on it); meanwhile the service reports ``starting``. A failed
  attempt is not retried for :data:`BROKER_RETRY_S` seconds. Once the broker answers, the
  child is launched by a :class:`LocalLauncher` with
  ``[interpreter, *descriptor.command, "--scope", <scope>, "--nats-url", <url>,
  "--token-stdin"]`` and logs to ``<cwd>/logs/services/<name>-<client instance[:8]>.log``
  (one file per Lightfall, so two Lightfalls on one host do not interleave). Plain
  ``nats://``, no TLS.
- **Local supervision:** the :class:`QtServiceClient` restarts a crashed child with backoff
  and kills/restarts a child that stopped heartbeating. If a local client loses the broker
  (``connection_changed(False)``: this manager's own broker crashed, or another Lightfall that
  owned the broker on that port exited), the manager re-probes the port and starts a broker
  there if none answers; clients and children reconnect to it by themselves. While any local
  client stays disconnected, the check repeats every :data:`BROKER_RETRY_S` seconds.
- **External** services (``launch="external"``) are connected when :meth:`start` runs
  (or on registration after it), through Lightfall's main IPC broker URL (the URL of the
  running :class:`IPCService`, see :meth:`set_main_ipc_url`), with a default TLS context
  unless that URL is local.

Per-service lifecycle (GUI thread, never blocking): :meth:`stop_service` stops one service
(its client and, for a local service, its child); :meth:`restart` stops it if running and
starts it again (re-reading the interpreter preference, and also retrying after a launcher
``gave_up`` or a first-spawn failure); ``client_created(name)`` fires whenever a new client
object exists, so consumers can connect to its signals without polling :meth:`client`.

:meth:`stop` stops every client concurrently: local children always get ``rpc.shutdown``
and a 3 s grace (a local child exits on stdin EOF, so ``stop_with_client=False`` cannot be
honoured for local services), then an owned broker is stopped.

The scope prefix (``ipc_topic_prefix``, default :data:`DEFAULT_IPC_TOPIC_PREFIX` like
IPCService; an explicitly empty value means ``"lightfall"``) is read once per manager; a
change takes effect after a Lightfall restart.
"""

from __future__ import annotations

import socket
import ssl
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

from loguru import logger
from PySide6.QtCore import QObject, QTimer, Signal

from lightfall_utils.ipc.local_server import LocalNatsServer, NatsPortInUseError, probe_nats
from lightfall_utils.ipc.services.descriptor import ServiceDescriptor
from lightfall_utils.ipc.services.launcher import LocalLauncher
from lightfall_utils.ipc.services.qt import QtServiceClient
from lightfall_utils.threads import initialize_main_thread_invoker, invoke_in_main_thread

DEFAULT_LOCAL_PORT = 4223
DEFAULT_SCOPE_PREFIX = "lightfall"
PROBE_TIMEOUT_S = 1.0
BROKER_RETRY_S = 10.0
_BROKER_JOIN_S = 8.0
_STOP_JOIN_S = 20.0  # QtServiceClient.stop() blocks up to 15 s plus a 5 s join
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _prefs():
    from lightfall.ui.preferences.manager import PreferencesManager

    return PreferencesManager.get_instance()


def _logs_dir() -> Path:
    # Matches utils/crash_diagnostics.py (Path.cwd() / "logs").
    return Path.cwd() / "logs"


def _is_local_url(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "") in _LOCAL_HOSTS
    except ValueError:
        return False


def _probe_or_start_broker(port: int) -> tuple[str | None, LocalNatsServer | None, str]:
    """Worker-thread body: ``(url, owned_server, error)``; ``url`` is None on failure."""
    url = f"nats://127.0.0.1:{port}"
    if probe_nats("127.0.0.1", port, PROBE_TIMEOUT_S) is not None:
        return url, None, ""
    server = LocalNatsServer(port=port)
    try:
        server.start()
    except NatsPortInUseError:
        if probe_nats("127.0.0.1", port, PROBE_TIMEOUT_S) is None:
            return None, None, f"local broker port {port} unavailable"
        return url, None, ""  # another Lightfall won the race; use its broker
    except Exception as e:
        logger.exception("Failed to start services local nats-server")
        return None, None, f"local broker failed to start: {e}"
    logger.info("Started services local nats-server on 127.0.0.1:{}", port)
    return url, server, ""


class ServiceManager(QObject):
    """Registry and lifecycle owner for out-of-process services."""

    services_changed = Signal()
    client_created = Signal(str)

    _instance: ClassVar[ServiceManager | None] = None
    _instance_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.client_instance = uuid.uuid4().hex
        self._descriptors: dict[str, ServiceDescriptor] = {}
        self._clients: dict[str, QtServiceClient] = {}
        self._errors: dict[str, str] = {}  # status overrides for services with no client
        self._started = False
        self._prefix: str | None = None
        self._main_url: str | None = None
        # local broker (all fields touched on the main thread only)
        self._local_server: LocalNatsServer | None = None
        self._local_url: str | None = None
        self._broker_thread: threading.Thread | None = None
        self._broker_result: tuple[str | None, LocalNatsServer | None, str] | None = None
        self._broker_gen = 0
        self._broker_error = ""
        self._broker_failed_at: float | None = None
        self._pending: set[str] = set()  # local services waiting for the broker
        self._stopping: dict[str, threading.Thread] = {}  # per-service stops in flight
        self._restart_after_stop: set[str] = set()
        self._recovery_scheduled = False

    # -- singleton ----------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> ServiceManager:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            inst, cls._instance = cls._instance, None
        if inst is not None:
            inst.stop()

    # -- configuration ------------------------------------------------------------------

    def set_main_ipc_url(self, url: str) -> None:
        """Use ``url`` (the running IPCService's URL) for external services."""
        self._main_url = url

    def _scope_prefix(self) -> str:
        if self._prefix is None:
            from lightfall.ipc.defaults import DEFAULT_IPC_TOPIC_PREFIX

            # Same default as IPCService, so external scopes match the services it sees.
            prefix = str(_prefs().get("ipc_topic_prefix", DEFAULT_IPC_TOPIC_PREFIX) or "")
            if not prefix:
                logger.info(
                    "ipc_topic_prefix is empty; services use scope prefix {!r}",
                    DEFAULT_SCOPE_PREFIX,
                )
                prefix = DEFAULT_SCOPE_PREFIX
            self._prefix = prefix
        return self._prefix

    def _scope(self, desc: ServiceDescriptor) -> str:
        return desc.resolved_scope(
            prefix=self._scope_prefix(),
            hostname=socket.gethostname(),
            client_instance=self.client_instance,
        )

    @staticmethod
    def _local_port() -> int:
        try:
            return int(_prefs().get("services_local_port", DEFAULT_LOCAL_PORT))
        except (TypeError, ValueError):
            logger.warning(
                "services_local_port is not a valid integer; using {}", DEFAULT_LOCAL_PORT
            )
            return DEFAULT_LOCAL_PORT

    def _main_ipc_url(self) -> str:
        if self._main_url is not None:
            return self._main_url
        # Not wired to a running IPCService (e.g. a test or a headless use): the pref.
        from lightfall.ipc.defaults import DEFAULT_IPC_NATS_URL

        return str(_prefs().get("ipc_nats_url", DEFAULT_IPC_NATS_URL) or "")

    @staticmethod
    def _interpreter(desc: ServiceDescriptor) -> str:
        if desc.interpreter_pref:
            value = _prefs().get(desc.interpreter_pref, "")
            if value:
                return str(value)
        return sys.executable

    @staticmethod
    def _app_version() -> str:
        import lightfall

        return lightfall.__version__

    # -- public API ---------------------------------------------------------------------

    def register(self, descriptor: ServiceDescriptor) -> None:
        """Register a service. External services connect now if :meth:`start` has run."""
        name = descriptor.name
        if descriptor.launch == "local" and not descriptor.stop_with_client:
            logger.warning(
                "Service {!r}: stop_with_client=False is ignored for local services "
                "(the child exits when its launcher's stdin closes)",
                name,
            )
        if name in self._descriptors:
            logger.warning("Service {!r} already registered; replacing descriptor", name)
            old = self._clients.pop(name, None)
            if old is not None:
                self._stop_clients({name: old})
        self._descriptors[name] = descriptor
        self._errors.pop(name, None)
        if self._started and descriptor.launch == "external":
            self._start_external(descriptor)
        self.services_changed.emit()

    def client(self, name: str) -> QtServiceClient | None:
        """The client for ``name``, or None if it is unknown or not started yet."""
        return self._clients.get(name)

    def ensure_started(self, name: str) -> None:
        """Start ``name`` if it is not running yet; returns immediately.

        Local services start here, lazily: the broker probe/start runs on a worker
        thread and the client is created on the GUI thread once the broker answers.

        Raises:
            KeyError: ``name`` is not registered.
        """
        desc = self._descriptors[name]
        if name in self._clients:
            return
        if name in self._stopping:
            # The old instance may still answer on the same scope: start once it is gone.
            self._restart_after_stop.add(name)
            self.services_changed.emit()
            return
        if desc.launch == "external":
            self._start_external(desc)
            return
        if self._local_url is not None:
            self._start_local(desc, self._local_url)
            return
        if (
            self._broker_failed_at is not None
            and time.monotonic() - self._broker_failed_at < BROKER_RETRY_S
        ):
            self._errors[name] = self._broker_error  # rate-limited: report the last failure
            self.services_changed.emit()
            return
        self._errors.pop(name, None)
        self._pending.add(name)
        if self._broker_thread is None:
            self._launch_broker_attempt()
        self.services_changed.emit()

    def stop_service(self, name: str) -> None:
        """Stop ``name`` (client and local child) without blocking the GUI thread.

        The stop (``rpc.shutdown``, grace, terminate) runs on a worker thread; the service
        shows ``unknown`` once stopping starts.  :meth:`ensure_started` or :meth:`restart`
        may be called right away: the new start waits for the old stop.

        Raises:
            KeyError: ``name`` is not registered.
        """
        self._descriptors[name]  # noqa: B018 - KeyError for unknown names
        self._pending.discard(name)
        self._restart_after_stop.discard(name)
        self._errors.pop(name, None)
        client = self._clients.pop(name, None)
        if client is not None:
            self._stop_async(name, client)
        self.services_changed.emit()

    def unregister(self, name: str) -> None:
        """Stop ``name`` (without blocking) and forget its descriptor; no-op if unknown."""
        if name not in self._descriptors:
            return
        self.stop_service(name)
        del self._descriptors[name]
        self.services_changed.emit()

    def restart(self, name: str) -> None:
        """Stop ``name`` if it runs, then start it again with a fresh client (and child).

        Also the retry path after a launcher ``gave_up``, a first-spawn failure or a broker
        failure (the broker rate limit is bypassed).  Returns immediately.

        Raises:
            KeyError: ``name`` is not registered.
        """
        self._descriptors[name]  # noqa: B018 - KeyError for unknown names
        self._broker_failed_at = None  # an explicit retry is not rate-limited
        self._errors.pop(name, None)
        client = self._clients.pop(name, None)
        if client is not None:
            self._stop_async(name, client)
        if name in self._stopping:
            self._restart_after_stop.add(name)
            self.services_changed.emit()
            return
        self.ensure_started(name)

    def statuses(self) -> list[dict]:
        out: list[dict] = []
        for name, desc in self._descriptors.items():
            client = self._clients.get(name)
            try:
                scope = client.scope if client is not None else self._scope(desc)
            except ValueError:
                scope = desc.scope
            if client is not None:
                info = client.info or {}
                health, last_error = client.health, client.last_error
                version = info.get("version", "")
                if desc.launch == "external" and health == "down" and desc.hint:
                    last_error = f"{last_error} ({desc.hint})" if last_error else desc.hint
            elif name in self._restart_after_stop or name in self._pending:
                health, last_error, version = "starting", "", ""
            elif name in self._stopping:
                health, last_error, version = "unknown", "stopping", ""
            elif name in self._errors:
                health, last_error, version = "down", self._errors[name], ""
            else:
                health, last_error, version = "unknown", "", ""
            out.append(
                {
                    "name": name,
                    "scope": scope,
                    "health": health,
                    "version": version,
                    "last_error": last_error,
                }
            )
        return out

    def start(self) -> None:
        """Connect every registered external service; local ones stay lazy."""
        self._started = True
        for desc in list(self._descriptors.values()):
            if desc.launch == "external" and desc.name not in self._clients:
                self._start_external(desc)

    def stop(self) -> None:
        """Stop every client (concurrently), then a broker this manager started. Idempotent."""
        self._started = False
        self._pending.clear()
        self._restart_after_stop.clear()
        stopping, self._stopping = dict(self._stopping), {}
        for t in stopping.values():
            t.join(_STOP_JOIN_S)
        self._broker_gen += 1  # any queued broker completion is now stale
        thread, self._broker_thread = self._broker_thread, None
        if thread is not None:
            thread.join(_BROKER_JOIN_S)
            result, self._broker_result = self._broker_result, None
            if result is not None and result[1] is not None:
                self._stop_server(result[1])  # the in-flight attempt started a broker
        clients, self._clients = self._clients, {}
        self._stop_clients(clients)
        server, self._local_server = self._local_server, None
        self._local_url = None
        if server is not None:
            self._stop_server(server)
        if clients:
            self.services_changed.emit()

    # -- internals: broker --------------------------------------------------------------

    def _launch_broker_attempt(self) -> None:
        initialize_main_thread_invoker()
        self._broker_gen += 1
        gen, port = self._broker_gen, self._local_port()
        self._broker_result = None

        def run() -> None:
            try:
                result = _probe_or_start_broker(port)
            except Exception as e:  # never leave the attempt hanging
                result = (None, None, f"local broker failed to start: {e}")
            self._broker_result = result
            invoke_in_main_thread(self._on_broker_done, gen)

        t = threading.Thread(target=run, name="svc-local-broker", daemon=True)
        self._broker_thread = t
        t.start()

    def _on_broker_done(self, gen: int) -> None:
        if gen != self._broker_gen:
            return  # stale (stop() ran, and handled any started broker)
        thread, self._broker_thread = self._broker_thread, None
        if thread is not None:
            thread.join(1.0)
        result, self._broker_result = self._broker_result, None
        if result is None:
            return
        url, server, error = result
        pending, self._pending = self._pending, set()
        if url is None:
            logger.error("Services local broker: {}", error)
            self._broker_error = error
            self._broker_failed_at = time.monotonic()
            for name, desc in self._descriptors.items():
                if desc.launch == "local" and name not in self._clients:
                    self._errors[name] = error
            self._schedule_broker_check(int(BROKER_RETRY_S * 1000))
            self.services_changed.emit()
            return
        old = self._local_server
        if server is not None:
            if old is not None and old is not server:
                self._stop_server(old)  # our previous broker died; this one replaces it
            self._local_server = server
        elif old is not None and not old.is_running():
            self._stop_server(old)  # it died and someone else's broker now answers
            self._local_server = None
        self._local_url = url
        self._broker_failed_at = None
        self._schedule_broker_check(int(BROKER_RETRY_S * 1000))
        for name in sorted(pending):
            desc = self._descriptors.get(name)
            if desc is not None and name not in self._clients:
                self._start_local(desc, url)
        self.services_changed.emit()

    def _local_clients(self) -> list[QtServiceClient]:
        return [
            c
            for name, c in self._clients.items()
            if self._descriptors.get(name) is not None and self._descriptors[name].launch == "local"
        ]

    def _on_local_connection(self, connected: bool) -> None:
        if not connected:
            self._schedule_broker_check(0)

    def _schedule_broker_check(self, delay_ms: int) -> None:
        if self._recovery_scheduled:
            return
        if not any(not c.broker_connected for c in self._local_clients()):
            return
        self._recovery_scheduled = True
        QTimer.singleShot(delay_ms, self._check_local_broker)

    def _check_local_broker(self) -> None:
        """A local client lost the broker: re-probe the port and start a broker if needed."""
        self._recovery_scheduled = False
        if self._local_url is None or self._broker_thread is not None:
            return
        if all(c.broker_connected for c in self._local_clients()):
            return  # everyone reconnected (e.g. the broker was restarted by someone else)
        logger.warning("Services local broker unreachable; re-probing {}", self._local_url)
        self._launch_broker_attempt()

    @staticmethod
    def _stop_server(server: LocalNatsServer) -> None:
        try:
            server.stop()
        except Exception:
            logger.exception("Error stopping the services local nats-server")

    # -- internals: clients -------------------------------------------------------------

    def _stop_clients(self, clients: dict[str, QtServiceClient]) -> None:
        """Stop ``clients`` in parallel (each blocks for its rpc.shutdown + grace)."""
        if not clients:
            return

        def stop_one(item: tuple[str, QtServiceClient]) -> None:
            name, client = item
            try:
                client.stop()
            except Exception:
                logger.exception("Error stopping service client {!r}", name)

        with ThreadPoolExecutor(
            max_workers=min(8, len(clients)), thread_name_prefix="svc-stop"
        ) as pool:
            list(pool.map(stop_one, clients.items()))
        for client in clients.values():
            self._detach(client)
            client.deleteLater()

    def _detach(self, client: QtServiceClient) -> None:
        for signal, slot in (
            (client.state_changed, self._on_client_state),
            (client.info_changed, self._on_client_info),
            (client.connection_changed, self._on_local_connection),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _stop_async(self, name: str, client: QtServiceClient) -> None:
        """Stop ``client`` on a worker thread; finish (and maybe restart) on the GUI thread."""
        initialize_main_thread_invoker()
        self._detach(client)

        def run() -> None:
            try:
                client.stop()
            except Exception:
                logger.exception("Error stopping service client {!r}", name)
            invoke_in_main_thread(self._on_stopped, name, client, threading.current_thread())

        t = threading.Thread(target=run, name=f"svc-stop-{name}", daemon=True)
        self._stopping[name] = t
        t.start()

    def _on_stopped(self, name: str, client: QtServiceClient, thread: threading.Thread) -> None:
        if self._stopping.get(name) is thread:
            del self._stopping[name]
        client.deleteLater()
        if name in self._restart_after_stop and name not in self._stopping:
            self._restart_after_stop.discard(name)
            if name in self._descriptors:
                self.ensure_started(name)
        self.services_changed.emit()

    def _on_client_state(self, _health: str) -> None:
        self.services_changed.emit()

    def _on_client_info(self, _info: dict) -> None:
        self.services_changed.emit()  # e.g. the version or a summary shown in the UI

    def _start_local(self, desc: ServiceDescriptor, url: str) -> None:
        try:
            scope = self._scope(desc)
        except ValueError as e:
            self._errors[desc.name] = str(e)
            self.services_changed.emit()
            return
        command = [
            self._interpreter(desc),
            *desc.command,
            "--scope",
            scope,
            "--nats-url",
            url,
            "--token-stdin",
        ]
        log_path = _logs_dir() / "services" / f"{desc.name}-{self.client_instance[:8]}.log"

        def make_launcher(token: str) -> LocalLauncher:
            return LocalLauncher(command, session_token=token, log_path=log_path)

        client = QtServiceClient(
            desc,
            url,
            scope=scope,
            app_name="lightfall",
            app_version=self._app_version(),
            launcher=make_launcher,
            tls=None,
            parent=self,
        )
        self._run_client(desc.name, client)

    def _start_external(self, desc: ServiceDescriptor) -> None:
        url = self._main_ipc_url()
        if not url:
            self._errors[desc.name] = "main IPC broker URL is not configured"
            self.services_changed.emit()
            return
        try:
            scope = self._scope(desc)
        except ValueError as e:
            self._errors[desc.name] = str(e)
            self.services_changed.emit()
            return
        tls = None if _is_local_url(url) else ssl.create_default_context()
        client = QtServiceClient(
            desc,
            url,
            scope=scope,
            app_name="lightfall",
            app_version=self._app_version(),
            tls=tls,
            parent=self,
        )
        self._run_client(desc.name, client)

    def _run_client(self, name: str, client: QtServiceClient) -> None:
        self._errors.pop(name, None)
        self._clients[name] = client
        client.state_changed.connect(self._on_client_state)
        client.info_changed.connect(self._on_client_info)
        desc = self._descriptors.get(name)
        if desc is not None and desc.launch == "local":
            client.connection_changed.connect(self._on_local_connection)
        client.start()
        self.client_created.emit(name)
        self.services_changed.emit()
