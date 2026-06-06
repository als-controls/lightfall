"""Shussebora data-movement service status panel.

Listens passively to shussebora heartbeats and transfer events over NATS
wildcard subjects (``shussebora.*.heartbeat`` etc.), so every instance on
the bus shows up without configuration, and offers an on-demand refresh via
the ``_shussebora.discover`` / ``<prefix>.status`` request-reply actions.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.utils.logging import logger

HEARTBEAT_STALE_S = 150  # ~2.5 missed 60 s heartbeats
HEARTBEAT_DEAD_S = 300
RECENT_LIMIT = 50

_COLOR_OK = "#2ecc71"
_COLOR_STALE = "#f39c12"
_COLOR_DEAD = "#e74c3c"
_COLOR_UNKNOWN = "#7f8c8d"


def _human_bytes(n: float | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def _human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class ShusseboraPanel(BasePanel):
    """Status of shussebora data-movement instances on the NATS bus."""

    panel_metadata = PanelMetadata(
        id="lightfall.panels.shussebora",
        name="Data Movement",
        description="Shussebora data-movement daemon status: transfers, queue, disk, EPICS triggers",
        icon="mdi6.truck-fast-outline",
        category="Monitoring",
        singleton=True,
        closable=True,
        keywords=["shussebora", "data movement", "transfer", "rsync", "daemon"],
        default_area="right",
        sidebar_order=20,
    )

    # Marshals background-thread request results onto the Qt main thread.
    sigStatusReceived = Signal(dict)
    sigTransfersReceived = Signal(list)

    def __init__(self, parent=None, ipc=None) -> None:
        self._ipc_override = ipc
        self._instances: dict[str, dict[str, Any]] = {}
        self._refreshing = False
        super().__init__(parent)

        self.sigStatusReceived.connect(self._apply_status)
        self.sigTransfersReceived.connect(self._apply_recent)

        self._stale_timer = QTimer(self)
        self._stale_timer.setInterval(10_000)
        self._stale_timer.timeout.connect(self._check_staleness)
        self._stale_timer.start()

        self._subscribe()

    # -- wiring -----------------------------------------------------------

    def _ipc(self):
        if self._ipc_override is not None:
            return self._ipc_override
        try:
            from lightfall.core.services import ServiceRegistry
            from lightfall.ipc.service import IPCService

            return ServiceRegistry.get_instance().get(IPCService, None)
        except Exception:
            return None

    def _subscribe(self) -> None:
        ipc = self._ipc()
        if ipc is None:
            self._hint_label.setText("IPC service unavailable — configure NATS in Preferences.")
            return
        ipc.subscribe("shussebora.*.heartbeat", self._on_heartbeat)
        ipc.subscribe("shussebora.*.transfer.complete", self._on_transfer_event)
        ipc.subscribe("shussebora.*.transfer.failed", self._on_transfer_event)
        ipc.subscribe("shussebora.*.epics.connected", self._on_epics_event)
        ipc.subscribe("shussebora.*.epics.disconnected", self._on_epics_event)

    def _on_closing(self) -> None:
        ipc = self._ipc()
        if ipc is None:
            return
        for subject in ("shussebora.*.heartbeat", "shussebora.*.transfer.complete",
                        "shussebora.*.transfer.failed", "shussebora.*.epics.connected",
                        "shussebora.*.epics.disconnected"):
            ipc.unsubscribe(subject)

    # -- UI ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        header = QHBoxLayout()
        self._hint_label = QLabel("Listening for shussebora instances…")
        header.addWidget(self._hint_label)
        header.addStretch()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_now)
        header.addWidget(refresh)
        self._layout.addLayout(header)

        self._instances_layout = QVBoxLayout()
        self._layout.addLayout(self._instances_layout)

        self._transfers_table = QTableWidget(0, 5)
        self._transfers_table.setHorizontalHeaderLabels(
            ["Time", "File", "Trigger", "Status", "Size"])
        self._transfers_table.horizontalHeader().setStretchLastSection(True)
        self._transfers_table.verticalHeader().setVisible(False)
        self._transfers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._transfers_table.setMinimumHeight(160)
        self._layout.addWidget(QLabel("Recent transfers"))
        self._layout.addWidget(self._transfers_table)
        self._layout.addStretch()

    def _ensure_card(self, hostname: str) -> dict[str, Any]:
        if hostname in self._instances:
            return self._instances[hostname]
        box = QGroupBox(hostname)
        layout = QVBoxLayout(box)
        title = QLabel()
        detail = QLabel()
        detail.setWordWrap(True)
        triggers = QLabel()
        triggers.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(triggers)
        self._instances_layout.addWidget(box)
        card = {"box": box, "title": title, "detail": detail, "triggers": triggers,
                "last_seen": None, "state": "unknown"}
        self._instances[hostname] = card
        self._hint_label.setText("")
        return card

    def _set_card_state(self, card: dict[str, Any], state: str, text: str) -> None:
        color = {"ok": _COLOR_OK, "stale": _COLOR_STALE,
                 "dead": _COLOR_DEAD}.get(state, _COLOR_UNKNOWN)
        card["state"] = state
        card["title"].setText(f'<span style="color:{color}">●</span> {text}')
        if state == "dead":
            self.set_sidebar_icon("", _COLOR_DEAD)
        elif all(c["state"] == "ok" for c in self._instances.values()):
            self.set_sidebar_icon("", "")

    # -- data handlers (Qt main thread) ------------------------------------

    def _on_heartbeat(self, subject: str, data: dict, reply: str | None) -> None:
        self._apply_status(data)

    def _apply_status(self, status: dict) -> None:
        hostname = status.get("hostname")
        if not hostname:
            return
        card = self._ensure_card(hostname)
        card["last_seen"] = time.time()

        counts = status.get("transfers", {})
        queue_depth = sum(t.get("queue_depth", 0) for t in status.get("triggers", []))
        self._set_card_state(card, "ok",
                             f"up {_human_duration(status.get('uptime_s'))}"
                             f" — v{status.get('version', '?')}")
        disk = status.get("disk", {})
        disk_text = (f"{disk.get('used_pct', '?')}% used, {_human_bytes(disk.get('free'))} free"
                     if "used_pct" in disk else disk.get("error", "?"))
        card["detail"].setText(
            f"disk: {disk_text}  |  queue: {queue_depth}  |  24h: "
            f"{counts.get('complete_24h', 0)} ok, {counts.get('failed_24h', 0)} failed, "
            f"{counts.get('pending', 0)} pending")
        self._render_triggers(card, status.get("triggers", []))

    def _render_triggers(self, card: dict[str, Any], triggers: list[dict]) -> None:
        parts = []
        for trig in triggers:
            mark = "✓" if trig.get("connected") else "✗"
            color = _COLOR_OK if trig.get("connected") else _COLOR_DEAD
            parts.append(f'<span style="color:{color}">{mark}</span> {trig.get("pv_prefix")}')
        card["triggers"].setText("  ".join(parts))
        card["trigger_data"] = triggers

    def _on_epics_event(self, subject: str, data: dict, reply: str | None) -> None:
        hostname = subject.split(".")[1]
        card = self._instances.get(hostname)
        if card is None:
            return
        connected = subject.endswith(".connected")
        triggers = card.get("trigger_data", [])
        for trig in triggers:
            if trig.get("pv_prefix") == data.get("pv_prefix"):
                trig["connected"] = connected
        self._render_triggers(card, triggers)

    def _on_transfer_event(self, subject: str, data: dict, reply: str | None) -> None:
        failed = subject.endswith(".failed")
        if failed and not data.get("final"):
            status = f"retry {data.get('attempts', '?')}"
        elif failed:
            status = "FAILED"
        else:
            status = "complete"
        self._prepend_transfer_row({
            "time": dt.datetime.now().strftime("%H:%M:%S"),
            "source": data.get("source", "?"),
            "trigger": data.get("trigger", "?"),
            "status": status,
            "bytes": data.get("bytes"),
        })

    def _apply_recent(self, transfers: list[dict]) -> None:
        self._transfers_table.setRowCount(0)
        for transfer in transfers:
            created = str(transfer.get("finished") or transfer.get("created") or "?")
            self._prepend_transfer_row({
                "time": created[11:19] if len(created) >= 19 else created,
                "source": transfer.get("source", "?"),
                "trigger": transfer.get("trigger", "?"),
                "status": transfer.get("status", "?"),
                "bytes": transfer.get("bytes"),
            }, append=True)

    def _prepend_transfer_row(self, row: dict, append: bool = False) -> None:
        table = self._transfers_table
        index = table.rowCount() if append else 0
        if not append:
            table.insertRow(0)
        else:
            table.insertRow(index)
        values = [row["time"], row["source"], row["trigger"], row["status"],
                  _human_bytes(row["bytes"]) if row["bytes"] is not None else ""]
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if row["status"] == "FAILED" or row["status"] == "failed":
                from PySide6.QtGui import QColor

                item.setForeground(QColor(_COLOR_DEAD))
            table.setItem(index, col, item)
        while table.rowCount() > RECENT_LIMIT:
            table.removeRow(table.rowCount() - 1)

    # -- staleness ----------------------------------------------------------

    def _check_staleness(self) -> None:
        now = time.time()
        for card in self._instances.values():
            if card["last_seen"] is None:
                continue
            age = now - card["last_seen"]
            if age > HEARTBEAT_DEAD_S:
                self._set_card_state(card, "dead",
                                     f"no heartbeat for {_human_duration(age)}")
            elif age > HEARTBEAT_STALE_S:
                self._set_card_state(card, "stale",
                                     f"heartbeat stale ({int(age)}s)")

    # -- active refresh -------------------------------------------------------

    def _on_activated(self) -> None:
        self._refresh_now()

    def _refresh_now(self) -> None:
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
                    self.sigStatusReceived.emit(status)
                recent = ipc.request(f"{prefix}.transfers.recent",
                                     {"limit": RECENT_LIMIT}, timeout_ms=3000)
                if recent and "transfers" in recent:
                    self.sigTransfersReceived.emit(recent["transfers"])
            except Exception as ex:  # pragma: no cover - defensive
                logger.warning("shussebora refresh failed: {}", ex)
            finally:
                self._refreshing = False

        threading.Thread(target=work, daemon=True, name="shussebora-refresh").start()

    # -- test/introspection API ------------------------------------------------

    def instance_count(self) -> int:
        return len(self._instances)

    def instance_state(self, hostname: str) -> str:
        return self._instances[hostname]["state"]

    def transfer_row(self, index: int) -> dict:
        table = self._transfers_table
        return {
            "time": table.item(index, 0).text(),
            "source": table.item(index, 1).text(),
            "trigger": table.item(index, 2).text(),
            "status": table.item(index, 3).text(),
        }

    def transfer_row_count(self) -> int:
        return self._transfers_table.rowCount()

    def _get_specific_introspection_data(self) -> dict:
        return {
            "instances": {
                host: {"state": card["state"], "last_seen": card["last_seen"]}
                for host, card in self._instances.items()
            },
            "recent_transfer_count": self.transfer_row_count(),
        }
