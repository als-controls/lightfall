"""NATS-based IPC service for Lightfall.

Provides a Qt-integrated wrapper around nats-py that handles connection
lifecycle, topic routing, pub/sub, and request/reply patterns.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import nats
from loguru import logger
from PySide6.QtCore import QObject, Signal

from lightfall.ipc.trust import TrustManager, TrustState
from lightfall.utils.threads import invoke_in_main_thread

__all__ = ["IPCService", "ActionInfo", "EventInfo", "_ActionHandle", "get_ipc_service"]


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
# _ActionHandle
# ---------------------------------------------------------------------------


class _ActionHandle:
    """Handle returned by :meth:`IPCService.register_action`.

    Calling :meth:`unregister` removes the action from the catalog and
    cancels its NATS subscription.
    """

    def __init__(self, service: IPCService, suffix: str, subject: str) -> None:
        self._service = service
        self._suffix = suffix
        self._subject = subject

    def unregister(self) -> None:
        """Remove this action from the catalog and unsubscribe."""
        self._service._action_catalog.pop(self._suffix, None)
        self._service.unsubscribe(self._subject)


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
        self._connectivity_error_logged: bool = False
        self._nc: nats.NATS | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()
        self._subscriptions: dict[str, _Subscription] = {}
        self._action_catalog: dict[str, ActionInfo] = {}
        self._event_catalog: dict[str, EventInfo] = {}
        self._trust: TrustManager | None = None
        self._instance_id = f"{platform.node()}-{os.getpid()}"
        self._display_name: str | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def instance_id(self) -> str:
        """Unique identity for this IPCService instance (``{hostname}-{pid}``)."""
        return self._instance_id

    @property
    def display_name(self) -> str | None:
        """Optional human-readable name for this instance."""
        return self._display_name

    @display_name.setter
    def display_name(self, value: str | None) -> None:
        self._display_name = value

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
    # Action & event catalog
    # ------------------------------------------------------------------

    def register_action(
        self,
        suffix: str,
        callback: Callable[[str, dict, str | None], Any],
        *,
        description: str = "",
        schema: dict[str, Any] | None = None,
        main_thread: bool = True,
    ) -> _ActionHandle:
        """Register a request/reply action handler.

        Args:
            suffix: Subject suffix (appended to the topic prefix).
            callback: Called as ``callback(subject, data, reply)`` for each
                incoming request.
            description: Human-readable description for meta-discovery.
            schema: Optional JSON Schema describing the request payload.
            main_thread: If True (default) the callback runs on the Qt main
                thread.

        Returns:
            An :class:`_ActionHandle` whose :meth:`~_ActionHandle.unregister`
            method removes both the catalog entry and the subscription.
        """
        full_subject = self.topic(suffix)
        self._action_catalog[suffix] = ActionInfo(
            subject=suffix, description=description, schema=schema
        )
        self.subscribe(full_subject, callback, main_thread=main_thread)
        return _ActionHandle(self, suffix, full_subject)

    def register_event(
        self,
        suffix: str,
        *,
        description: str = "",
        schema: dict[str, Any] | None = None,
    ) -> None:
        """Register an event in the event catalog (no subscription created).

        This is catalog-only — it exists so that remote clients can discover
        which events this service may publish via ``list_events()`` / the
        ``meta.events`` endpoint.

        Args:
            suffix: Subject suffix used when publishing the event.
            description: Human-readable description for meta-discovery.
            schema: Optional JSON Schema describing the event payload.
        """
        self._event_catalog[suffix] = EventInfo(
            subject=suffix, description=description, schema=schema
        )

    def list_actions(self) -> list[dict[str, Any]]:
        """Return catalog metadata for all registered actions."""
        return [
            {"subject": info.subject, "description": info.description, "schema": info.schema}
            for info in self._action_catalog.values()
        ]

    def list_events(self) -> list[dict[str, Any]]:
        """Return catalog metadata for all registered events."""
        return [
            {"subject": info.subject, "description": info.description, "schema": info.schema}
            for info in self._event_catalog.values()
        ]

    # ------------------------------------------------------------------
    # Meta-discovery endpoints
    # ------------------------------------------------------------------

    def _handle_meta_actions(
        self, subject: str, data: dict, reply: str | None
    ) -> None:
        """Respond to a ``meta.actions`` request with the action catalog."""
        if reply:
            self.reply(reply, {
                "instance_id": self._instance_id,
                "display_name": self._display_name,
                "prefix": self._topic_prefix,
                "actions": self.list_actions(),
            })

    def _handle_meta_events(
        self, subject: str, data: dict, reply: str | None
    ) -> None:
        """Respond to a ``meta.events`` request with the event catalog."""
        if reply:
            self.reply(reply, {
                "instance_id": self._instance_id,
                "display_name": self._display_name,
                "prefix": self._topic_prefix,
                "events": self.list_events(),
            })

    def _handle_discover(self, subject: str, data: dict, reply: str | None) -> None:
        """Respond to ``_lightfall.discover`` with instance identity and actions."""
        if reply:
            self.reply(reply, {
                "instance_id": self._instance_id,
                "display_name": self._display_name,
                "prefix": self._topic_prefix,
                "actions": self.list_actions(),
            })

    def register_meta_endpoints(self) -> None:
        """Register ``meta.actions`` and ``meta.events`` discovery endpoints."""
        self.register_action(
            "meta.actions",
            self._handle_meta_actions,
            description="List all registered actions",
        )
        self.register_action(
            "meta.events",
            self._handle_meta_events,
            description="List all registered events",
        )
        # Well-known discovery subject (not prefixed)
        self.subscribe("_lightfall.discover", self._handle_discover, main_thread=False)

    # -- Trust & auth --

    def set_trust_manager(self, trust: TrustManager) -> None:
        """Store the trust manager to use for application trust evaluation."""
        self._trust = trust

    def evaluate_trust(self, app_name: str) -> TrustState:
        """Evaluate the trust state for *app_name*.

        Delegates to the configured :class:`TrustManager`.  Returns
        :attr:`TrustState.DENIED` when no trust manager has been set.
        """
        if self._trust is None:
            return TrustState.DENIED
        return self._trust.check(app_name)

    def build_auth_response(
        self,
        *,
        approved: bool,
        session=None,
        tiled_url: str = "",
        reason: str = "",
    ) -> dict:
        """Build a response dict for an auth handshake.

        Args:
            approved: Whether the connection was approved. When True, the
                cached Tiled API key is read from :class:`SessionManager`
                (auth-v2); ``session`` is still required to gate the
                approved/denied branch.
            session: Session object; used as a presence flag for the
                approved branch. Its ``.token`` attribute is no longer
                read — the credential comes from the API-key cache.
            tiled_url: Tiled server URL to include in an approved response.
            reason: Optional denial reason; only included when non-empty.

        Returns:
            A plain dict ready to be JSON-serialised and sent over IPC.

        Note:
            The field name ``"tiled_token"`` is preserved as a public IPC
            contract (see ``docs/ipc-architecture.md`` /
            ``docs/ipc-client-guide.md``). Its *value* is now a Tiled API
            key, not a Keycloak bearer token — external clients consume it
            as ``api_key=tiled_token`` when building their Tiled client,
            which is the intended usage. The field rename is deferred to
            the auth cleanup plan, coordinated with IPC client updates.
        """
        if approved and session is not None:
            from lightfall.auth.session import SessionManager

            return {
                "status": "approved",
                # Historical name; actually carries an API key under auth-v2.
                "tiled_token": SessionManager.get_instance().get_api_key("tiled"),
                "tiled_url": tiled_url,
            }
        response: dict = {"status": "denied"}
        if reason:
            response["reason"] = reason
        return response

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

    def request(
        self, subject: str, data: dict, timeout_ms: int = 1000
    ) -> dict | None:
        """Send a request and wait for a reply.

        Thread-safe — can be called from any thread including the Qt main
        thread.  Blocks the *calling* thread for up to *timeout_ms*.

        Args:
            subject: NATS subject to send the request to.
            data: JSON-serialisable request payload.
            timeout_ms: Maximum time to wait for a reply in milliseconds.

        Returns:
            Decoded JSON reply dict, or ``None`` on timeout / error /
            not connected.
        """
        if not self.is_connected or self._loop is None or self._nc is None:
            return None

        payload = json.dumps(data).encode()
        timeout = timeout_ms / 1000.0

        future = asyncio.run_coroutine_threadsafe(
            self._do_request(payload, subject, timeout), self._loop
        )
        try:
            result = future.result(timeout=timeout + 1.0)
            return result
        except Exception as exc:
            logger.warning("IPCService: request to '{}' failed: {}", subject, exc)
            return None

    async def _do_request(
        self, payload: bytes, subject: str, timeout: float
    ) -> dict | None:
        """Async helper — execute NATS request/reply."""
        if self._nc is None:
            return None
        try:
            msg = await self._nc.request(subject, payload, timeout=timeout)
            return json.loads(msg.data.decode())
        except TimeoutError:
            logger.debug("IPCService: request to '{}' timed out", subject)
            return None
        except Exception as exc:
            logger.warning("IPCService: request error on '{}': {}", subject, exc)
            return None

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
        # str(exc) is often empty for nats errors (StaleConnection, SlowConsumer, ...)
        msg = str(exc) or type(exc).__name__
        # Connectivity errors fire on every reconnect attempt while the broker
        # is unreachable. Log the first one, then stay silent until we
        # reconnect (cleared in _reconnected_cb).
        if isinstance(
            exc,
            (
                nats.errors.NoServersError,
                nats.errors.ConnectionClosedError,
                nats.errors.StaleConnectionError,
                TimeoutError,
            ),
        ):
            if self._connectivity_error_logged:
                return
            self._connectivity_error_logged = True
            logger.warning(
                "IPCService: NATS {} (further reconnect errors suppressed until reconnect)",
                msg,
            )
        elif isinstance(exc, nats.errors.SlowConsumerError):
            logger.warning("IPCService: NATS {}", msg)
        else:
            logger.error(
                "IPCService: NATS error: {} ({})", msg, type(exc).__name__
            )

    async def _disconnected_cb(self) -> None:
        logger.warning("IPCService: disconnected from NATS")
        with self._connected_lock:
            self._connected = False
        invoke_in_main_thread(self.sigConnectionChanged.emit, False)

    async def _reconnected_cb(self) -> None:
        logger.info("IPCService: reconnected to NATS")
        with self._connected_lock:
            self._connected = True
        self._connectivity_error_logged = False
        invoke_in_main_thread(self.sigConnectionChanged.emit, True)


# ---------------------------------------------------------------------------
# Module-level convenience accessor
# ---------------------------------------------------------------------------


def get_ipc_service() -> IPCService | None:
    """Return the application's IPCService instance, or None if unavailable.

    Uses the :class:`~lightfall.core.services.ServiceRegistry` singleton to look
    up the registered :class:`IPCService`.
    """
    from lightfall.core.services import ServiceRegistry

    registry = ServiceRegistry.get_instance()
    return registry.get(IPCService, None)
