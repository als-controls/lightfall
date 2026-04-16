"""NATS <-> bluesky plan bridge.

Translates async NATS subscriptions into poll-able queues a generator-based
plan can drain via try_get(), yielding bps.sleep() between polls.
"""

from __future__ import annotations

import queue
from typing import Any


class NATSPlanBridge:
    """Buffers NATS messages into thread-safe queues for synchronous plan polling.

    The bridge subscribes to NATS subjects on behalf of a running plan. Incoming
    messages land in an internal Queue. The plan drains the queue with
    try_get() and yields bps.sleep() between polls, keeping the RunEngine
    responsive.
    """

    def __init__(self, ipc_service: Any) -> None:
        self._ipc = ipc_service
        self._queues: dict[str, queue.Queue] = {}
        self._subscriptions: list[Any] = []

    def subscribe(self, subject: str) -> None:
        """Subscribe to a subject; incoming messages queue for try_get()."""
        if subject in self._queues:
            return
        q: queue.Queue = queue.Queue()
        self._queues[subject] = q

        def handler(msg_subject: str, data: dict, reply: str | None) -> None:
            q.put(data)

        sub = self._ipc.subscribe(subject, callback=handler, main_thread=False)
        self._subscriptions.append(sub)

    def try_get(self, subject: str) -> dict | None:
        """Non-blocking: return next message or None if queue is empty."""
        q = self._queues.get(subject)
        if q is None:
            return None
        try:
            return q.get_nowait()
        except queue.Empty:
            return None

    def publish(self, subject: str, payload: dict) -> None:
        """Publish a message via the IPC service. Fire-and-forget."""
        self._ipc.publish(subject, payload)

    def cleanup(self) -> None:
        """Unsubscribe all subscriptions. Call from the plan's finally block."""
        for sub in self._subscriptions:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        self._subscriptions.clear()
        self._queues.clear()
