"""Live logbook updates: subscribe to server change events over NATS and pull.

Notify-and-pull — the NATS event only signals "something changed"; the actual
data comes from the client's normal authz-scoped pull.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer

from lightfall.logbook.client import LogbookClient, fetch_server_user_id
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

_SUBJECT_PREFIX = "_lightfall.logbook.changed."


def logbook_user_token(user_id: str) -> str:
    """Hex-encode the server user_id into a NATS-subject-safe token.

    Must match ``lightfall_logbook.events.subject_for_user`` on the server.
    """
    return user_id.encode("utf-8").hex()


def subject_for_user(user_id: str) -> str:
    return _SUBJECT_PREFIX + logbook_user_token(user_id)


def get_ipc_service():
    """Indirection so tests can monkeypatch the IPC accessor."""
    from lightfall.ipc.service import get_ipc_service as _g
    return _g()


class LogbookLiveUpdates(QObject):
    """Subscribes to server change events and triggers pulls.

    Lifecycle: ``start(server_url)`` once after the client is initialised;
    ``on_user_changed(server_url)`` when the session user changes; ``stop()``
    on teardown.
    """

    FALLBACK_POLL_MS = 60_000

    def __init__(self, client: LogbookClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._subject: str | None = None
        self._poll = QTimer(self)
        self._poll.setInterval(self.FALLBACK_POLL_MS)
        self._poll.timeout.connect(self._on_poll_tick)
        self._connected_signal_wired = False

    # -- lifecycle --------------------------------------------------------

    def start(self, server_url: str | None) -> None:
        if not server_url:
            return
        self._poll.start()
        ipc = get_ipc_service()
        if ipc is not None and not self._connected_signal_wired:
            try:
                ipc.sigConnectionChanged.connect(self._on_connection_changed)
                self._connected_signal_wired = True
            except Exception:
                pass
        self._resolve_and_subscribe(server_url)

    def on_user_changed(self, server_url: str | None) -> None:
        if server_url:
            self._resolve_and_subscribe(server_url)

    def stop(self) -> None:
        self._poll.stop()
        ipc = get_ipc_service()
        if ipc is not None and self._subject is not None:
            ipc.unsubscribe(self._subject)
        self._subject = None

    # -- internals --------------------------------------------------------

    def _resolve_and_subscribe(self, server_url: str) -> None:
        # Learn the server's user_id off the main thread, then subscribe.
        user_id = None
        try:
            from lightfall.auth.session import SessionManager
            user = SessionManager.get_instance().current_user
            user_id = user.username if user else None
        except Exception:
            pass
        QThreadFuture(
            fetch_server_user_id,
            server_url,
            user_id,
            callback_slot=self._subscribe_for,
            key="logbook-live-id",
            name="logbook-live-id",
        ).start()

    def _subscribe_for(self, server_user_id: str | None) -> None:
        if not server_user_id:
            return
        ipc = get_ipc_service()
        if ipc is None:
            return
        new_subject = subject_for_user(server_user_id)
        if new_subject == self._subject:
            return
        if self._subject is not None:
            ipc.unsubscribe(self._subject)
        ipc.subscribe(new_subject, self._on_event, main_thread=True)
        self._subject = new_subject
        logger.info("Logbook live updates subscribed: {}", new_subject)

    def _on_event(self, subject: str, data: dict[str, Any], reply: str | None) -> None:
        self._client.schedule_sync()

    def _on_connection_changed(self, connected: bool) -> None:
        if connected:
            self._client.schedule_sync()

    def _on_poll_tick(self) -> None:
        self._client.schedule_sync()
