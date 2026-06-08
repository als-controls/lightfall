"""Shussebora data-movement service status panel.

All NATS state arrives via the shared :class:`ShusseboraMonitor` singleton
(which owns the wildcard subscriptions — IPCService allows one subscriber
per subject), so this panel and the status bar indicator stay consistent.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lightfall.services.shussebora_monitor import RECENT_LIMIT, ShusseboraMonitor
from lightfall.ui.panels.base import BasePanel, PanelMetadata

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
        icon="mdi6.snail",
        category="Monitoring",
        singleton=True,
        closable=True,
        keywords=["shussebora", "data movement", "transfer", "rsync", "daemon"],
        default_area="right",
        sidebar_order=20,
    )

    def __init__(self, parent=None, monitor: ShusseboraMonitor | None = None) -> None:
        self._monitor = monitor if monitor is not None else ShusseboraMonitor.get_instance()
        self._cards: dict[str, dict[str, Any]] = {}
        super().__init__(parent)

        if not self._monitor.start():
            self._hint_label.setText("IPC service unavailable — configure NATS in Preferences.")
        self._monitor.sigStatus.connect(self._apply_status)
        self._monitor.sigStateChanged.connect(self._on_state_changed)
        self._monitor.sigTransfer.connect(self._on_transfer_event)
        self._monitor.sigEpics.connect(self._on_epics_event)
        self._monitor.sigRecentTransfers.connect(self._apply_recent)

        # Replay anything the monitor saw before this panel existed.
        for inst in self._monitor.instances().values():
            if inst["status"]:
                self._apply_status(inst["status"])

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
        self._transfers_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
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
        if hostname in self._cards:
            return self._cards[hostname]
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
                "state": "unknown", "title_text": ""}
        self._cards[hostname] = card
        self._hint_label.setText("")
        return card

    def _render_title(self, card: dict[str, Any]) -> None:
        color = {"ok": _COLOR_OK, "stale": _COLOR_STALE,
                 "dead": _COLOR_DEAD}.get(card["state"], _COLOR_UNKNOWN)
        card["title"].setText(f'<span style="color:{color}">●</span> {card["title_text"]}')

    # -- monitor signal handlers -------------------------------------------

    def _apply_status(self, status: dict) -> None:
        hostname = status.get("hostname")
        if not hostname:
            return
        card = self._ensure_card(hostname)
        card["state"] = "ok"
        card["title_text"] = (f"up {_human_duration(status.get('uptime_s'))}"
                              f" — v{status.get('version', '?')}")
        self._render_title(card)

        counts = status.get("transfers", {})
        queue_depth = sum(t.get("queue_depth", 0) for t in status.get("triggers", []))
        disk = status.get("disk", {})
        disk_text = (f"{disk.get('used_pct', '?')}% used, {_human_bytes(disk.get('free'))} free"
                     if "used_pct" in disk else disk.get("error", "?"))
        card["detail"].setText(
            f"disk: {disk_text}  |  queue: {queue_depth}  |  24h: "
            f"{counts.get('complete_24h', 0)} ok, {counts.get('failed_24h', 0)} failed, "
            f"{counts.get('pending', 0)} pending")
        self._render_triggers(card, status.get("triggers", []))

    def _on_state_changed(self, hostname: str, state: str) -> None:
        card = self._cards.get(hostname)
        if card is None:
            return
        card["state"] = state
        if state == "dead":
            card["title_text"] = "no heartbeat — service down?"
            self.set_sidebar_icon("", _COLOR_DEAD)
        elif state == "stale":
            card["title_text"] = "heartbeat stale"
        elif self._monitor.worst_state() == "ok":
            self.set_sidebar_icon("", "")
        self._render_title(card)

    def _render_triggers(self, card: dict[str, Any], triggers: list[dict]) -> None:
        parts = []
        for trig in triggers:
            mark = "✓" if trig.get("connected") else "✗"
            color = _COLOR_OK if trig.get("connected") else _COLOR_DEAD
            parts.append(f'<span style="color:{color}">{mark}</span> {trig.get("pv_prefix")}')
        card["triggers"].setText("  ".join(parts))

    def _on_epics_event(self, hostname: str, connected: bool, data: dict) -> None:
        card = self._cards.get(hostname)
        if card is None:
            return
        # The monitor already updated its cached trigger states.
        inst = self._monitor.instances().get(hostname, {})
        self._render_triggers(card, inst.get("status", {}).get("triggers", []))

    def _on_transfer_event(self, kind: str, data: dict) -> None:
        if kind == "failed" and not data.get("final"):
            status = f"retry {data.get('attempts', '?')}"
        elif kind == "failed":
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
        table.insertRow(index)
        values = [row["time"], row["source"], row["trigger"], row["status"],
                  _human_bytes(row["bytes"]) if row["bytes"] is not None else ""]
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if row["status"] in ("FAILED", "failed"):
                from PySide6.QtGui import QColor

                item.setForeground(QColor(_COLOR_DEAD))
            table.setItem(index, col, item)
        while table.rowCount() > RECENT_LIMIT:
            table.removeRow(table.rowCount() - 1)

    # -- refresh ----------------------------------------------------------

    def _on_activated(self) -> None:
        self._refresh_now()

    def _refresh_now(self) -> None:
        self._monitor.refresh()

    # -- test/introspection API ---------------------------------------------

    def instance_count(self) -> int:
        return len(self._cards)

    def instance_state(self, hostname: str) -> str:
        return self._cards[hostname]["state"]

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
                host: {"state": inst["state"], "last_seen": inst["last_seen"]}
                for host, inst in self._monitor.instances().items()
            },
            "recent_transfer_count": self.transfer_row_count(),
        }
