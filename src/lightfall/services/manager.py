"""ServiceManager: owns Lightfall's out-of-process services (IPC services pattern).

Each registered :class:`~lightfall_utils.ipc.services.descriptor.ServiceDescriptor` gets
one :class:`~lightfall_utils.ipc.services.qt.QtServiceClient`:

- **Local** services (``launch="local"``) start lazily, on the first
  :meth:`ServiceManager.ensure_started`. The manager makes sure a host-local NATS broker
  answers on ``127.0.0.1:<services_local_port>`` (default 4223): if ``probe_nats`` fails it
  starts a :class:`LocalNatsServer` and owns it. The child is launched by a
  :class:`LocalLauncher` with
  ``[interpreter, *descriptor.command, "--scope", <scope>, "--nats-url", <url>,
  "--token-stdin"]`` and logs to ``<cwd>/logs/services/<name>.log``. Plain ``nats://``,
  no TLS.
- **External** services (``launch="external"``) are connected when :meth:`start` runs
  (or on registration after it), through Lightfall's main IPC broker URL, with a default
  TLS context unless that URL is local.

:meth:`stop` stops every client: local children get ``rpc.shutdown`` and a 3 s grace
(unless the descriptor says ``stop_with_client=False``), then an owned broker is stopped.
"""

from __future__ import annotations

import socket
import ssl
import sys
import threading
import uuid
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

from loguru import logger
from PySide6.QtCore import QObject, Signal

from lightfall_utils.ipc.local_server import LocalNatsServer, NatsPortInUseError, probe_nats
from lightfall_utils.ipc.services.descriptor import ServiceDescriptor
from lightfall_utils.ipc.services.launcher import LocalLauncher
from lightfall_utils.ipc.services.qt import QtServiceClient

DEFAULT_LOCAL_PORT = 4223
DEFAULT_SCOPE_PREFIX = "lightfall"
PROBE_TIMEOUT_S = 1.0
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
        self._local_server: LocalNatsServer | None = None
        self._local_url: str | None = None  # set once the local broker answers
        self._prefix: str | None = None

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

    @staticmethod
    def _main_ipc_url() -> str:
        from lightfall.ipc.defaults import DEFAULT_IPC_NATS_URL

        prefs = _prefs()
        if prefs.get("ipc_use_local_nats", False):
            try:
                port = int(prefs.get("ipc_local_nats_port", 4222))
            except (TypeError, ValueError):
                port = 4222
            return f"nats://127.0.0.1:{port}"
        return str(prefs.get("ipc_nats_url", DEFAULT_IPC_NATS_URL) or "")

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
        if name in self._descriptors:
            logger.warning("Service {!r} already registered; replacing descriptor", name)
            old = self._clients.pop(name, None)
            if old is not None:
                self._stop_client(name, old)
        self._descriptors[name] = descriptor
        self._errors.pop(name, None)
        if self._started and descriptor.launch == "external":
            self._start_external(descriptor)
        self.services_changed.emit()

    def client(self, name: str) -> QtServiceClient | None:
        return self._clients.get(name)

    def ensure_started(self, name: str) -> None:
        """Start ``name`` if it is not running yet (local services start here, lazily)."""
        desc = self._descriptors[name]  # KeyError for unknown services
        if name in self._clients:
            return
        if desc.launch == "external":
            self._start_external(desc)
            return
        url = self._ensure_local_broker()
        if url is None:
            self.services_changed.emit()
            return
        self._start_local(desc, url)

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
        """Stop every client, then a local broker this manager started. Idempotent."""
        self._started = False
        clients, self._clients = self._clients, {}
        for name, client in clients.items():
            self._stop_client(name, client)
        server, self._local_server = self._local_server, None
        self._local_url = None
        if server is not None:
            try:
                server.stop()
            except Exception:
                logger.exception("Error stopping the services local nats-server")
        if clients:
            self.services_changed.emit()

    # -- internals ----------------------------------------------------------------------

    def _stop_client(self, name: str, client: QtServiceClient) -> None:
        desc = self._descriptors.get(name)
        if desc is not None and desc.launch == "local" and not desc.stop_with_client:
            # stop_with_client=False: leave the child running. QtServiceClient.stop()
            # always stops its launcher, so detach the launcher first and only close the
            # client connection. (The child still exits when this process ends, since
            # its stdin pipe closes.)
            client._launcher = None
        try:
            client.stop()
        except Exception:
            logger.exception("Error stopping service client {!r}", name)
        try:
            client.state_changed.disconnect(self._on_client_state)
        except (RuntimeError, TypeError):
            pass
        client.deleteLater()

    def _on_client_state(self, _health: str) -> None:
        self.services_changed.emit()

    def _ensure_local_broker(self) -> str | None:
        """Return the local broker URL, starting a broker if nothing answers; None on failure."""
        if self._local_url is not None:
            return self._local_url
        port = self._local_port()
        url = f"nats://127.0.0.1:{port}"
        if probe_nats("127.0.0.1", port, PROBE_TIMEOUT_S) is None:
            server = LocalNatsServer(port=port)
            try:
                server.start()
                self._local_server = server
                logger.info("Started services local nats-server on 127.0.0.1:{}", port)
            except NatsPortInUseError:
                if probe_nats("127.0.0.1", port, PROBE_TIMEOUT_S) is None:
                    self._mark_local_down(f"local broker port {port} unavailable")
                    return None
                # Another Lightfall won the race; use its broker.
            except Exception as e:
                logger.exception("Failed to start services local nats-server")
                self._mark_local_down(f"local broker failed to start: {e}")
                return None
        self._local_url = url
        for name, desc in self._descriptors.items():
            if desc.launch == "local":
                self._errors.pop(name, None)
        return url

    def _mark_local_down(self, error: str) -> None:
        logger.error("Services local broker: {}", error)
        for name, desc in self._descriptors.items():
            if desc.launch == "local" and name not in self._clients:
                self._errors[name] = error

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
