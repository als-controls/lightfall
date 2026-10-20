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
  "--token-stdin"]`` and logs to ``<cwd>/logs/services/<name>.log``. Plain ``nats://``,
  no TLS.
- **External** services (``launch="external"``) are connected when :meth:`start` runs
  (or on registration after it), through Lightfall's main IPC broker URL (the URL of the
  running :class:`IPCService`, see :meth:`set_main_ipc_url`), with a default TLS context
  unless that URL is local.

:meth:`stop` stops every client concurrently: local children always get ``rpc.shutdown``
and a 3 s grace (a local child exits on stdin EOF, so ``stop_with_client=False`` cannot be
honoured for local services), then an owned broker is stopped.
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
from PySide6.QtCore import QObject, Signal

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
            prefix = str(_prefs().get("ipc_topic_prefix", "") or "")
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
            elif name in self._pending:
                health, last_error, version = "starting", "", ""
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
            self.services_changed.emit()
            return
        self._local_url = url
        self._local_server = server
        self._broker_failed_at = None
        for name in sorted(pending):
            desc = self._descriptors.get(name)
            if desc is not None and name not in self._clients:
                self._start_local(desc, url)
        self.services_changed.emit()

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
            try:
                client.state_changed.disconnect(self._on_client_state)
            except (RuntimeError, TypeError):
                pass
            client.deleteLater()

    def _on_client_state(self, _health: str) -> None:
        self.services_changed.emit()

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
        log_path = _logs_dir() / "services" / f"{desc.name}.log"

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
        client.start()
        self.services_changed.emit()
