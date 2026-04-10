"""NATS-based IPC service for LUCID.

Provides a Qt-integrated wrapper around nats-py that handles connection
lifecycle, topic routing, pub/sub, and request/reply patterns.
"""

from __future__ import annotations

import asyncio
import json
import ssl
import threading
from dataclasses import dataclass
from typing import Any, Callable

import nats
from loguru import logger
from PySide6.QtCore import QObject, Signal

from lucid.utils.threads import invoke_in_main_thread

__all__ = ["IPCService", "ActionInfo", "EventInfo"]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ActionInfo:
    """Metadata for an IPC action (request/reply subject)."""

    subject: str
    description: str = ""
    schema: dict[str, Any] | None = None


@dataclass
class EventInfo:
    """Metadata for an IPC event (publish/subscribe subject)."""

    subject: str
    description: str = ""
    schema: dict[str, Any] | None = None


@dataclass
class _Subscription:
    """Internal subscription record."""

    subject: str
    callback: Callable[[str, dict, str | None], Any]
    main_thread: bool
    nats_sub: Any  # nats.aio.subscription.Subscription | None


# ---------------------------------------------------------------------------
# IPCService
# ---------------------------------------------------------------------------


class IPCService(QObject):
    """NATS-backed IPC service with Qt integration.

    Runs a dedicated asyncio event loop on a background daemon thread so that
    NATS async I/O does not block the Qt main thread.

    Signals:
        sigConnectionChanged(bool): Emitted whenever the NATS connection state
            changes.  True = connected, False = disconnected.
    """

    sigConnectionChanged = Signal(bool)

    def __init__(
        self,
        nats_url: str,
        topic_prefix: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._nats_url = nats_url
        self._topic_prefix = topic_prefix

        self._connected_lock = threading.Lock()
        self._connected: bool = False
        self._nc: nats.NATS | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()
        self._subscriptions: dict[str, _Subscription] = {}

    # ------------------------------------------------------------------
    # Topic builder
    # ------------------------------------------------------------------

    def topic(self, suffix: str) -> str:
        """Build a full subject by joining the configured prefix and *suffix*.

        If the prefix is empty the suffix is returned unchanged (no leading dot).
        """
        if self._topic_prefix:
            return f"{self._topic_prefix}.{suffix}"
        return suffix

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True when the NATS client is connected."""
        with self._connected_lock:
            return self._connected

    def start(self) -> None:
        """Connect to NATS on a background thread.

        Does nothing when ``nats_url`` is empty or already running.
        """
        if not self._nats_url:
            return
        if self._thread is not None:
            return

        self._shutdown_event.clear()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="ipc-nats",
        )
        self._thread.start()

    def stop(self) -> None:
        """Disconnect from NATS and join the background thread."""
        if self._thread is None:
            return

        self._shutdown_event.set()

        if self._loop is not None and self._nc is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._drain_and_close(), self._loop
            )
            try:
                future.result(timeout=5)
            except Exception as exc:
                logger.warning("IPCService: error during drain/close: {}", exc)

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

        self._thread.join(timeout=5)
        self._thread = None
        self._loop = None

    # ------------------------------------------------------------------
    # Subscribe / publish / request
    # ------------------------------------------------------------------

    def subscribe(
        self,
        subject: str,
        callback: Callable[[str, dict, str | None], Any],
        *,
        main_thread: bool = True,
    ) -> None:
        """Register *callback* for messages on *subject*.

        If the service is already connected the NATS subscription is created
        immediately; otherwise it is deferred until the connection is established.

        Args:
            subject: NATS subject to subscribe to.
            callback: Called as ``callback(subject, data, reply)`` where *data*
                is the decoded JSON payload and *reply* is the reply-to subject
                (or ``None``).
            main_thread: If True (default) the callback is dispatched to the Qt
                main thread via ``invoke_in_main_thread``.
        """
        sub = _Subscription(subject=subject, callback=callback, main_thread=main_thread, nats_sub=None)
        self._subscriptions[subject] = sub

        if self.is_connected and self._loop is not None and self._nc is not None:
            asyncio.run_coroutine_threadsafe(
                self._create_nats_sub(subject, sub), self._loop
            )

    def unsubscribe(self, subject: str) -> None:
        """Remove the subscription for *subject*.

        Safe to call even when *subject* was never subscribed.
        """
        sub = self._subscriptions.pop(subject, None)
        if sub is None:
            return

        if sub.nats_sub is not None and self._loop is not None:
            asyncio.run_coroutine_threadsafe(
                self._drain_sub(sub.nats_sub), self._loop
            )

    def publish(self, subject: str, data: dict) -> None:
        """JSON-encode *data* and publish to *subject*.

        Dropped silently when not connected.
        """
        if not self.is_connected or self._loop is None or self._nc is None:
            return

        payload = json.dumps(data).encode()
        asyncio.run_coroutine_threadsafe(
            self._nc.publish(subject, payload), self._loop
        )

    def reply(self, reply_subject: str | None, data: dict) -> None:
        """Send a reply to *reply_subject*.

        No-op when *reply_subject* is falsy (``None`` or empty string).
        """
        if not reply_subject:
            return
        self.publish(reply_subject, data)

    # ------------------------------------------------------------------
    # Internal: event loop runner
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Entry point for the background daemon thread."""
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_serve())
        except Exception as exc:
            logger.error("IPCService: event loop exited with error: {}", exc)
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _connect_and_serve(self) -> None:
        """Open NATS connection, re-subscribe, then wait for shutdown."""
        try:
            tls_ctx = ssl.create_default_context()
            self._nc = await nats.connect(
                self._nats_url,
                tls=tls_ctx,
                error_cb=self._error_cb,
                disconnected_cb=self._disconnected_cb,
                reconnected_cb=self._reconnected_cb,
            )
        except Exception as exc:
            logger.error("IPCService: failed to connect to NATS at {}: {}", self._nats_url, exc)
            return

        with self._connected_lock:
            self._connected = True
        invoke_in_main_thread(self.sigConnectionChanged.emit, True)
        logger.info("IPCService: connected to NATS at {}", self._nats_url)

        # Re-create any subscriptions that were registered before connect
        for subject, sub in list(self._subscriptions.items()):
            await self._create_nats_sub(subject, sub)

        # Wait until stop() signals shutdown
        while not self._shutdown_event.is_set():
            await asyncio.sleep(0.1)

    async def _drain_and_close(self) -> None:
        """Drain pending messages and close the NATS connection."""
        if self._nc is None:
            return
        try:
            await self._nc.drain()
        except Exception as exc:
            logger.debug("IPCService: drain error (may be normal during shutdown): {}", exc)
        with self._connected_lock:
            was_connected = self._connected
            self._connected = False
        if was_connected:
            invoke_in_main_thread(self.sigConnectionChanged.emit, False)

    async def _create_nats_sub(self, subject: str, sub: _Subscription) -> None:
        """Create a NATS subscription and store the handle in *sub*."""
        if self._nc is None:
            return
        handler = self._make_handler(sub)
        try:
            nats_sub = await self._nc.subscribe(subject, cb=handler)
            sub.nats_sub = nats_sub
        except Exception as exc:
            logger.error("IPCService: failed to subscribe to {}: {}", subject, exc)

    @staticmethod
    async def _drain_sub(nats_sub: Any) -> None:
        """Drain a single NATS subscription."""
        try:
            await nats_sub.drain()
        except Exception as exc:
            logger.debug("IPCService: error draining sub: {}", exc)

    # ------------------------------------------------------------------
    # Internal: message handler factory
    # ------------------------------------------------------------------

    def _make_handler(self, sub: _Subscription) -> Callable:
        """Return an async NATS message handler for *sub*."""
        subject = sub.subject

        async def handler(msg: Any) -> None:
            reply: str = msg.reply if msg.reply else ""

            # Decode JSON payload
            try:
                data = json.loads(msg.data.decode())
            except Exception as exc:
                logger.warning(
                    "IPCService: malformed JSON on subject '{}': {}", subject, exc
                )
                if reply:
                    error_payload = json.dumps({"error": "malformed JSON"}).encode()
                    try:
                        await self._nc.publish(reply, error_payload)
                    except Exception:
                        pass
                return

            try:
                if sub.main_thread:
                    invoke_in_main_thread(sub.callback, subject, data, reply)
                else:
                    sub.callback(subject, data, reply)
            except Exception as exc:
                logger.error(
                    "IPCService: unhandled exception in callback for '{}': {}",
                    subject,
                    exc,
                )
                logger.exception(exc)
                if reply:
                    error_payload = json.dumps({"error": str(exc)}).encode()
                    if self._loop is not None and self._nc is not None:
                        asyncio.run_coroutine_threadsafe(
                            self._nc.publish(reply, error_payload),
                            self._loop,
                        )

        return handler

    # ------------------------------------------------------------------
    # Internal: NATS callbacks
    # ------------------------------------------------------------------

    async def _error_cb(self, exc: Exception) -> None:
        logger.error("IPCService: NATS error: {}", exc)

    async def _disconnected_cb(self) -> None:
        logger.warning("IPCService: disconnected from NATS")
        with self._connected_lock:
            self._connected = False
        invoke_in_main_thread(self.sigConnectionChanged.emit, False)

    async def _reconnected_cb(self) -> None:
        logger.info("IPCService: reconnected to NATS")
        with self._connected_lock:
            self._connected = True
        invoke_in_main_thread(self.sigConnectionChanged.emit, True)
