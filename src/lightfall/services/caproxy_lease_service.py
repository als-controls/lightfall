"""Caproxy attested-lease service for NCS.

Talks to caproxy-server's lease API (``POST /api/leases/request``,
``GET /api/leases``) so the Lightfall UI can request an unlock and show
live lease status. See docs/plans/2026-07-23-caproxy-lease-ux.md.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx
from PySide6.QtCore import QObject, Signal

from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture, QThreadFutureIterator

DEFAULT_CAPROXY_URL = "http://localhost:8000"

# Polling cadence for GET /api/leases.
POLL_INTERVAL_S = 3.0

# ThreadManager key so a repeated start_polling() call replaces (cancels)
# any prior polling thread rather than running two loops concurrently.
_POLL_THREAD_KEY = "caproxy_lease_poll"

_REQUEST_TIMEOUT_S = 10.0
_POLL_TIMEOUT_S = 10.0


def _load_caproxy_url_pref() -> str | None:
    """Read the configured caproxy URL from PreferencesManager.

    Returns None on any failure (manager uninitialised, etc.), so this is
    trivially monkeypatchable / safe to call before the app is fully up.
    """
    from lightfall.ui.preferences.manager import PreferencesManager

    prefs = PreferencesManager.get_instance()
    return prefs.get("caproxy_url", None)


def get_caproxy_base_url() -> str:
    """Return the configured caproxy base URL, defaulting to localhost:8000."""
    try:
        value = _load_caproxy_url_pref()
    except Exception:
        return DEFAULT_CAPROXY_URL
    return value or DEFAULT_CAPROXY_URL


def _resolve_token() -> str | None:
    """Resolve the bearer token: caproxy_token pref, else SessionManager token.

    Order:
    1. ``caproxy_token`` preference, if set (pilot/dev override).
    2. ``SessionManager`` session.token, if authenticated and non-None
       (auth-v2 logins mint per-service keys and set session.token to
       None, so this degrades gracefully to "no token").
    3. None (unauthenticated request).
    """
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        pref_token = PreferencesManager.get_instance().get("caproxy_token", None)
        if pref_token:
            return pref_token
    except Exception:
        pass

    try:
        from lightfall.auth.session import AuthState, SessionManager

        session_manager = SessionManager.get_instance()
        if session_manager.state == AuthState.AUTHENTICATED:
            session = session_manager.session
            if session is not None and session.token:
                return session.token
    except Exception:
        pass

    return None


def _auth_headers() -> dict[str, str]:
    token = _resolve_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _extract_error_text(response: httpx.Response) -> str:
    """Best-effort extraction of the server's {"error": ...} message body."""
    try:
        data = response.json()
        if isinstance(data, dict) and "error" in data:
            return str(data["error"])
    except Exception:
        pass
    text = response.text.strip()
    return text or f"HTTP {response.status_code}"


class CaproxyLeaseService(QObject):
    """Service for requesting and polling caproxy attested leases.

    Signals:
        request_finished: Emitted with the decoded response dict on a
            successful (2xx) lease request.
        request_failed: Emitted with the server's error text (or a network
            failure message) when a lease request fails.
        leases_updated: Emitted with the full lease list whenever it changes
            from the last known snapshot.
        poll_error: Emitted once per failure streak (not every poll tick)
            when the polling loop cannot reach the server.
    """

    request_finished = Signal(dict)
    request_failed = Signal(str)
    leases_updated = Signal(list)
    poll_error = Signal(str)

    _instance: CaproxyLeaseService | None = None
    _lock = threading.RLock()

    def __init__(self) -> None:
        super().__init__()
        self._request_thread: QThreadFuture | None = None
        self._poll_thread: QThreadFuture | None = None
        self._poll_stop_event = threading.Event()
        self._last_leases: list[Any] | None = None
        self._poll_failing = False

    @classmethod
    def get_instance(cls) -> CaproxyLeaseService:
        """Get the singleton CaproxyLeaseService instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.stop_polling()
                cls._instance.deleteLater()
            cls._instance = None

    # ------------------------------------------------------------------
    # Request lease
    # ------------------------------------------------------------------

    def request_lease(
        self,
        pv_patterns: list[str],
        duration_s: float,
        bounds_min: float | None = None,
        bounds_max: float | None = None,
        note: str = "",
    ) -> None:
        """Request an attested lease. Runs the POST off the GUI thread.

        Emits ``request_finished(dict)`` on a 2xx response, or
        ``request_failed(str)`` with the server's error text (or a
        network-failure message) otherwise. Never raises into Qt.
        """
        payload: dict[str, Any] = {
            "pv_patterns": list(pv_patterns),
            "duration_s": duration_s,
        }
        if bounds_min is not None:
            payload["bounds_min"] = bounds_min
        if bounds_max is not None:
            payload["bounds_max"] = bounds_max
        if note:
            payload["note"] = note

        base_url = get_caproxy_base_url()
        headers = _auth_headers()

        self._request_thread = QThreadFuture(
            self._do_request_lease,
            base_url,
            headers,
            payload,
            callback_slot=self._on_request_result,
            name="caproxy_request_lease",
        )
        self._request_thread.start()

    @staticmethod
    def _do_request_lease(
        base_url: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> tuple[bool, Any]:
        """Runs in a background thread. Never raises — returns (ok, data)."""
        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
                response = client.post(
                    f"{base_url.rstrip('/')}/api/leases/request",
                    json=payload,
                    headers=headers,
                )
            if response.status_code // 100 == 2:
                try:
                    return True, response.json()
                except Exception as exc:
                    return False, f"Malformed response from server: {exc}"
            return False, _extract_error_text(response)
        except Exception as exc:
            logger.warning("caproxy lease request failed: {}", exc)
            return False, f"Request failed: {exc}"

    def _on_request_result(self, result: tuple[bool, Any]) -> None:
        ok, data = result
        if ok:
            self.request_finished.emit(data)
        else:
            self.request_failed.emit(str(data))

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def start_polling(self) -> None:
        """Start (or restart) the 3s lease-polling loop in a background thread.

        Registered under a fixed ThreadManager key, so calling this again
        (e.g. on reconnect) cancels any prior polling thread rather than
        running two loops concurrently.
        """
        self._poll_stop_event = threading.Event()
        self._last_leases = None
        self._poll_failing = False

        stop_event = self._poll_stop_event
        base_url = get_caproxy_base_url()

        self._poll_thread = QThreadFutureIterator(
            self._poll_loop,
            base_url,
            stop_event,
            yield_slot=self._on_poll_tick,
            interrupt_callable=stop_event.set,
            key=_POLL_THREAD_KEY,
            name="caproxy_lease_poll",
        )
        self._poll_thread.start()

    def stop_polling(self) -> None:
        """Stop the polling loop, unblocking any in-flight sleep/HTTP wait."""
        self._poll_stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.cancel()
            self._poll_thread = None

    def _poll_loop(self, base_url: str, stop_event: threading.Event):
        """Generator run in the background thread: yields a tick result per cycle.

        Each yielded value is delivered to ``_on_poll_tick`` via sigResult
        (regular QThreadFuture emits on every yield, not just the final
        return). ``stop_event`` doubles as the interrupt_callable target and
        the sleep-unblocker, and also bounds the httpx call via timeout.
        """
        headers = _auth_headers()
        while not stop_event.is_set():
            try:
                with httpx.Client(timeout=_POLL_TIMEOUT_S) as client:
                    response = client.get(
                        f"{base_url.rstrip('/')}/api/leases", headers=headers
                    )
                if response.status_code // 100 == 2:
                    yield (True, response.json())
                else:
                    yield (False, _extract_error_text(response))
            except Exception as exc:
                yield (False, f"Poll failed: {exc}")

            # Interruptible sleep: wait() returns True immediately once
            # stop_event is set, unblocking cancellation promptly instead
            # of waiting out the full interval.
            stop_event.wait(POLL_INTERVAL_S)

    def _on_poll_tick(self, result: tuple[bool, Any]) -> None:
        ok, data = result
        if ok:
            self._poll_failing = False
            leases = data if isinstance(data, list) else data.get("leases", [])
            if leases != self._last_leases:
                self._last_leases = leases
                self.leases_updated.emit(leases)
        else:
            if not self._poll_failing:
                self._poll_failing = True
                self.poll_error.emit(str(data))
