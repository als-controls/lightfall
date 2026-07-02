# src/lightfall/visualization/stream_bridge.py
"""Bridges a Tiled streaming subscription to the Qt GUI thread.

The Tiled subscription callback fires on a background daemon thread; this
QObject re-emits each update as a Qt signal so GUI-thread slots can update
widgets safely. Only emit here — never touch widgets on the callback thread.
"""
from __future__ import annotations

from typing import Any

from loguru import logger
from PySide6.QtCore import QObject, Signal


class StreamBridge(QObject):
    update_received = Signal(object)  # emitted on the GUI thread (queued)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sub: Any | None = None

    def connect_node(self, node: Any) -> None:
        """Subscribe to a Tiled node's stream; emit update_received per push."""
        self.disconnect()
        # Subscription API per Task 1's recorded form (container-level if available,
        # else per-child). The prior spike proved: sub = node.subscribe();
        # sub.new_data.add_callback(cb); sub.start_in_thread(start=1).
        sub = node.subscribe()
        sub_new_data = getattr(sub, "new_data", None)
        if sub_new_data is not None:           # tiled ArraySubscription style
            sub_new_data.add_callback(self._on_update)
        else:                                   # _FakeSub / alt style
            sub.add_callback(self._on_update)
        sub.start_in_thread(start=1)
        self._sub = sub

    def _on_update(self, update: Any) -> None:
        # BACKGROUND THREAD. Do not touch widgets. Signal is queued to GUI thread.
        self.update_received.emit(update)

    def disconnect(self) -> None:
        if self._sub is not None:
            try:
                self._sub.disconnect()
            except Exception as e:
                logger.warning("StreamBridge disconnect error: {}", e)
            self._sub = None
