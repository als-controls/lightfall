"""Shared monitor for shussebora data-movement daemons on the NATS bus.

IPCService keys subscriptions by subject, so only one object may subscribe
to the ``shussebora.*`` wildcard subjects. This singleton owns those
subscriptions and fans the state out as Qt signals to any number of
consumers (the Data Movement panel, the status bar indicator).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from lightfall.utils.logging import logger

HEARTBEAT_STALE_S = 150  # ~2.5 missed 60 s heartbeats
HEARTBEAT_DEAD_S = 300
RECENT_LIMIT = 50

_SUFFIXES = (
    "heartbeat",
    "transfer.complete",
    "transfer.failed",
    "epics.connected",
    "epics.disconnected",
)
_NEW_SUFFIXES = (
    "crucible.upload.complete",
    "crucible.upload.failed",
    "disk.pressure",
)

# Legacy ``shussebora.<host>.<suffix>`` and per-beamline
# ``shussebora.<beamline token>.<host>.<suffix>`` forms are both followed
# during the migration. Instance identity is the payload's ``hostname``.
SUBJECTS = (
    tuple(f"shussebora.*.{s}" for s in _SUFFIXES)
    + tuple(f"shussebora.*.*.{s}" for s in _SUFFIXES + _NEW_SUFFIXES)
)


def _subject_host(subject: str, suffix: str) -> str:
    """Hostname token from a subject of either form (fallback for payloads lacking it)."""
    parts = subject.split(".")
    n_suffix = len(suffix.split("."))
    body = parts[1:len(parts) - n_suffix]
    return body[-1] if body else ""


class ShusseboraMonitor(QObject):
    """Tracks every shussebora instance seen on the bus.

    Signals:
        sigStatus: Full status payload received (heartbeat or active refresh).
        sigTransfer: ('complete' | 'failed', payload) transfer event.
        sigEpics: (hostname, connected, payload) EPICS trigger transition.
        sigStateChanged: (hostname, 'ok' | 'stale' | 'dead') heartbeat health.
        sigRecentTransfers: transfers.recent reply rows from an active refresh.
        sigDiskPressure: (hostname, payload) disk.pressure event.
        sigCrucible: ('complete' | 'failed', payload) Crucible upload event.
    """

    sigStatus = Signal(dict)
    sigTransfer = Signal(str, dict)
    sigEpics = Signal(str, bool, dict)
    sigStateChanged = Signal(str, str)
    sigRecentTransfers = Signal(list)
    sigDiskPressure = Signal(str, dict)
    sigCrucible = Signal(str, dict)

    # Internal: marshals background-thread request results to the main thread.
    _sigIngestStatus = Signal(dict)

    _instance: ShusseboraMonitor | None = None

    @classmethod
    def get_instance(cls) -> ShusseboraMonitor:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def __init__(self, ipc=None, parent=None) -> None:
        super().__init__(parent)
        self._ipc_override = ipc
        self._instances: dict[str, dict[str, Any]] = {}
        self._started = False
        self._refreshing = False
        self._pressure: dict[str, dict[str, Any]] = {}

        self._sigIngestStatus.connect(self.ingest_status)

        self._stale_timer = QTimer(self)
        self._stale_timer.setInterval(10_000)
        self._stale_timer.timeout.connect(self._check_staleness)

    def _ipc(self):
        if self._ipc_override is not None:
            return self._ipc_override
        try:
            from lightfall.core.services import ServiceRegistry
            from lightfall.ipc.service import IPCService

            return ServiceRegistry.get_instance().get(IPCService, None)
        except Exception:
            return None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> bool:
        """Subscribe to the shussebora subjects. Idempotent."""
        if self._started:
            return True
        ipc = self._ipc()
        if ipc is None:
            return False
        handlers = {
            "heartbeat": self._on_heartbeat,
            "transfer.": self._on_transfer,
            "epics.": self._on_epics,
            "crucible.": self._on_crucible,
            "disk.pressure": self._on_disk_pressure,
        }
        for subject in SUBJECTS:
            suffix = subject.split("*.")[-1]
            for prefix, handler in handlers.items():
                if suffix.startswith(prefix):
                    ipc.subscribe(subject, handler)
                    break
        self._stale_timer.start()
        self._started = True
        return True

    def stop(self) -> None:
        ipc = self._ipc()
        if ipc is not None:
            for subject in SUBJECTS:
                ipc.unsubscribe(subject)
        self._stale_timer.stop()
        self._started = False

    # -- state --------------------------------------------------------------

    def instances(self) -> dict[str, dict[str, Any]]:
        """Snapshot of known instances: hostname -> {state, last_seen, status}."""
        return {host: dict(inst) for host, inst in self._instances.items()}

    def worst_state(self) -> str:
        """Aggregate health across instances: dead > stale > unknown > ok."""
        states = [inst["state"] for inst in self._instances.values()]
        if not states:
            return "unknown"
        for state in ("dead", "stale", "unknown"):
            if state in states:
                return state
        return "ok"

    def disk_pressure(self) -> dict[str, dict[str, Any]]:
        """Hosts currently reporting disk pressure: hostname -> payload."""
        return dict(self._pressure)

    def ingest_status(self, status: dict) -> None:
        """Record a status payload (from heartbeat or active refresh)."""
        hostname = status.get("hostname")
        if not hostname:
            return
        inst = self._instances.setdefault(
            hostname, {"state": "unknown", "last_seen": None, "status": {}})
        inst["last_seen"] = time.time()
        inst["status"] = status
        disk = status.get("disk")
        if isinstance(disk, dict) and "pressure" in disk:
            if disk["pressure"]:
                self._pressure[hostname] = dict(disk)
            else:
                self._pressure.pop(hostname, None)
        self.sigStatus.emit(status)
        self._set_state(hostname, "ok")

    def _set_state(self, hostname: str, state: str) -> None:
        inst = self._instances[hostname]
        if inst["state"] != state:
            inst["state"] = state
            self.sigStateChanged.emit(hostname, state)

    def _check_staleness(self) -> None:
        now = time.time()
        for hostname, inst in self._instances.items():
            if inst["last_seen"] is None:
                continue
            age = now - inst["last_seen"]
            if age > HEARTBEAT_DEAD_S:
                self._set_state(hostname, "dead")
            elif age > HEARTBEAT_STALE_S:
                self._set_state(hostname, "stale")

    # -- NATS handlers (Qt main thread via IPCService) ------------------------

    def _on_heartbeat(self, subject: str, data: dict, reply: str | None) -> None:
        self.ingest_status(data)

    def _on_transfer(self, subject: str, data: dict, reply: str | None) -> None:
        kind = "failed" if subject.endswith(".failed") else "complete"
        self.sigTransfer.emit(kind, data)

    def _on_epics(self, subject: str, data: dict, reply: str | None) -> None:
        connected = subject.endswith(".connected")
        suffix = "epics.connected" if connected else "epics.disconnected"
        hostname = data.get("hostname") or _subject_host(subject, suffix)
        triggers = self._instances.get(hostname, {}).get("status", {}).get("triggers", [])
        for trig in triggers:
            if trig.get("pv_prefix") == data.get("pv_prefix"):
                trig["connected"] = connected
        self.sigEpics.emit(hostname, connected, data)

    def _on_crucible(self, subject: str, data: dict, reply: str | None) -> None:
        kind = "failed" if subject.endswith(".failed") else "complete"
        self.sigCrucible.emit(kind, data)

    def _on_disk_pressure(self, subject: str, data: dict, reply: str | None) -> None:
        hostname = data.get("hostname") or _subject_host(subject, "disk.pressure")
        self._pressure[hostname] = data
        self.sigDiskPressure.emit(hostname, data)

    # -- active refresh --------------------------------------------------------

    def refresh(self) -> None:
        """Discover an instance and pull status + recent transfers (background thread)."""
        ipc = self._ipc()
        if ipc is None or not getattr(ipc, "is_connected", False) or self._refreshing:
            return
        self._refreshing = True

        def work() -> None:
            try:
                info = ipc.request("_shussebora.discover", {}, timeout_ms=2000)
                if not info:
                    return
                prefix = info.get("prefix")
                status = ipc.request(f"{prefix}.status", {}, timeout_ms=3000)
                if status:
                    self._sigIngestStatus.emit(status)
                recent = ipc.request(f"{prefix}.transfers.recent",
                                     {"limit": RECENT_LIMIT}, timeout_ms=3000)
                if recent and "transfers" in recent:
                    self.sigRecentTransfers.emit(recent["transfers"])
            except Exception as ex:  # pragma: no cover - defensive
                logger.warning("shussebora refresh failed: {}", ex)
            finally:
                self._refreshing = False

        threading.Thread(target=work, daemon=True, name="shussebora-refresh").start()
