"""
Event-based fragment injection service.

Listens to LUCID system events (device changes, RunEngine documents) and
creates readonly fragments in the current logbook entry via ``LogbookClient``.
"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from lucid.utils.logging import logger


class EventListener(QObject):
    """Singleton that bridges LUCID events → logbook readonly fragments.

    Call :meth:`start` after the ``LogbookClient`` is initialised.

    Signals:
        fragment_injected(entry_id): Emitted after a readonly fragment is
            written to the DB, so the panel can refresh.
    """

    fragment_injected = Signal(str)  # entry_id

    _instance: EventListener | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self._current_entry_id: str | None = None
        self._current_run_uid: str | None = None
        self._started = False

    @classmethod
    def get_instance(cls) -> EventListener:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def current_entry_id(self) -> str | None:
        return self._current_entry_id

    @current_entry_id.setter
    def current_entry_id(self, value: str | None) -> None:
        self._current_entry_id = value

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._connect_action_logger()
        self._connect_run_engine()
        logger.info("EventListener started (entry_id={})", self._current_entry_id)

    def _connect_action_logger(self) -> None:
        try:
            from lucid.logbook import DeviceActionLogger
            dal = DeviceActionLogger.get_instance()
            dal.group_closed.connect(self._on_action_group_closed)
            dal.group_updated.connect(self._on_action_group_updated)
            logger.info("EventListener connected to DeviceActionLogger")
        except Exception as exc:
            logger.warning("Could not connect to DeviceActionLogger: {}", exc)

    @Slot(object)
    def _on_action_group_updated(self, group: Any) -> None:
        """Log each new action as it happens (live injection)."""
        if not self._current_entry_id:
            return
        actions = getattr(group, "actions", [])
        if not actions:
            return
        # Only log the latest action (previous ones were already logged)
        action = actions[-1]
        group_id = getattr(group, "id", "")
        frag_id = f"action-{group_id}-{len(actions) - 1}"

        # Check if we already logged this one
        from lucid.logbook.client import LogbookClient
        client = LogbookClient.get_instance()
        existing = client.list_fragments(self._current_entry_id)
        if any(f["id"] == frag_id for f in existing):
            return

        data = {
            "device_name": getattr(action, "device_name", "?"),
            "old_value": str(getattr(action, "old_value", None)),
            "new_value": str(getattr(action, "new_value", None)),
            "action_type": getattr(action, "action_type", "set"),
            "unit": getattr(action, "unit", ""),
        }
        try:
            client.add_fragment(
                self._current_entry_id,
                kind="readonly",
                subtype="device_change",
                content=f"{data['device_name']}: {data['old_value']} → {data['new_value']}",
                data=data,
                fragment_id=frag_id,
            )
            logger.debug("Logged device change: {}", data["device_name"])
            self.fragment_injected.emit(self._current_entry_id)
        except Exception as e:
            logger.error("Failed to log device change: {}", e)

    @Slot(object)
    def _on_action_group_closed(self, group: Any) -> None:
        """Group finalized — no additional action needed since group_updated handles live injection."""
        logger.debug("Action group closed: {}", getattr(group, "id", "?"))

    def _connect_run_engine(self) -> None:
        try:
            from lucid.acquire import get_run_engine
            re = get_run_engine()
            re.sigDocumentYield.connect(self._on_run_document)
            logger.info("EventListener connected to RunEngine")
        except Exception as exc:
            logger.warning("Could not connect to RunEngine: {}", exc)

    @Slot(str, dict)
    def _on_run_document(self, name: str, doc: dict) -> None:
        if not self._current_entry_id:
            return
        from lucid.logbook.client import LogbookClient
        client = LogbookClient.get_instance()

        try:
            if name == "start":
                uid = doc.get("uid", "")
                plan_name = doc.get("plan_name", "unknown")
                self._current_run_uid = uid
                data = {
                    "plan_name": plan_name,
                    "params": {k: v for k, v in doc.items()
                               if k not in ("uid", "time", "plan_name", "plan_type")},
                    "uid": uid,
                }
                client.add_fragment(
                    self._current_entry_id,
                    kind="readonly",
                    subtype="bluesky_plan",
                    content=f"Plan: {plan_name} ({uid[:8]})",
                    data=data,
                )
                self.fragment_injected.emit(self._current_entry_id)
            elif name == "stop":
                exit_status = doc.get("exit_status", "unknown")
                run_uid = doc.get("run_start", "")
                data = {
                    "exit_status": exit_status,
                    "uid": run_uid,
                    "num_events": doc.get("num_events", {}),
                }
                client.add_fragment(
                    self._current_entry_id,
                    kind="readonly",
                    subtype="bluesky_plan",
                    content=f"Run {run_uid[:8]} {exit_status}",
                    data=data,
                )
                self.fragment_injected.emit(self._current_entry_id)
                self._current_run_uid = None
        except Exception as e:
            logger.error("Failed to log run document: {}", e)
