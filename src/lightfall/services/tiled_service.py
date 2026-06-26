"""Tiled data catalog service for NCS.

Manages connection to Tiled server and TiledWriter callback lifecycle
for persisting bluesky documents to the Tiled data catalog.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal

from lightfall.services.tiled_writer_patch import TiledWriter
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

if TYPE_CHECKING:
    pass


DEFAULT_TILED_URL = "http://bcgtiled.dhcp.lbl.gov:8000"


def _load_tiled_url_pref() -> str | None:
    """Read the configured Tiled URL from PreferencesManager.

    Returns None on any failure (manager uninitialised, ConfigManager
    missing, etc.). Wrapped so it's trivially monkeypatchable in tests.
    """
    from lightfall.ui.preferences.manager import PreferencesManager
    prefs = PreferencesManager.get_instance()
    return prefs.get("tiled_url", None)


def get_tiled_base_url() -> str:
    """Return the configured Tiled base URL (bare host, no /api/v1 suffix).

    The returned value is the *server root*, e.g. "http://bcgtiled.dhcp.lbl.gov:8000".
    Callers that need the API root must append "/api/v1" themselves.
    """
    try:
        value = _load_tiled_url_pref()
    except Exception:
        return DEFAULT_TILED_URL
    return value or DEFAULT_TILED_URL


class _SettingsAdapter:
    """Adapter so AccessStamper sees access_override on a single object."""

    @property
    def access_override(self):
        from lightfall.ui.preferences.manager import PreferencesManager
        from lightfall.ui.preferences.tiled_settings import access_override_from_prefs

        prefs = PreferencesManager.get_instance()
        return access_override_from_prefs(prefs)


class TiledConnectionState(Enum):
    """Connection state for the Tiled service."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class TiledAuthMode(Enum):
    """Authentication mode for Tiled connection."""

    NONE = "none"  # No authentication
    API_KEY = "api_key"  # Use API key
    KEYCLOAK = "keycloak"  # Use Keycloak tokens from SessionManager


@dataclass
class TiledConfig:
    """Configuration for Tiled connection."""

    url: str = ""
    api_key: str | None = None
    enabled: bool = False
    auth_mode: TiledAuthMode = TiledAuthMode.NONE


class TiledService(QObject):
    """Service managing Tiled connection and TiledWriter lifecycle.

    TiledService provides:
    - Connection management to Tiled server
    - Automatic TiledWriter subscription to Engine
    - Health checking with automatic reconnection
    - Status signals for UI updates

    Signals:
        connection_changed: Emitted when connection state changes (state, message).

    Example:
        >>> service = TiledService.get_instance()
        >>> service.configure(url="http://localhost:8000", api_key=None, enabled=True)
        >>> service.connect()
        >>> # Service automatically subscribes TiledWriter to Engine
    """

    connection_changed = Signal(object, str)  # (TiledConnectionState, message)

    _instance: TiledService | None = None
    _lock = threading.RLock()

    # Health check interval in milliseconds
    HEALTH_CHECK_INTERVAL_MS = 30000

    def __init__(self) -> None:
        """Initialize the Tiled service."""
        super().__init__()
        self._config = TiledConfig()
        self._state = TiledConnectionState.DISCONNECTED
        self._client: Any = None
        # When True, an external client was adopted (adopt_client); auto-connects
        # (e.g. the Keycloak auth handler) must not clobber it.
        self._adopted: bool = False
        self._writer: Any = None
        self._subscription_token: int | None = None
        self._error_message: str = ""
        self._connect_thread: QThreadFuture | None = None
        self._session_connected = False

        # Health check timer
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._health_check)

        logger.debug("TiledService initialized")

    @classmethod
    def get_instance(cls) -> TiledService:
        """Get the singleton TiledService instance."""
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
                cls._instance.disconnect()
                cls._instance.deleteLater()
            cls._instance = None

    @property
    def state(self) -> TiledConnectionState:
        """Current connection state."""
        return self._state

    @property
    def config(self) -> TiledConfig:
        """Current configuration."""
        return self._config

    @property
    def is_connected(self) -> bool:
        """Whether currently connected to Tiled server."""
        return self._state == TiledConnectionState.CONNECTED

    @property
    def client(self) -> Any:
        """The connected Tiled root client, or ``None`` if not connected."""
        return self._client

    @property
    def server_version(self) -> str | None:
        """Tiled ``library_version`` reported by the connected server, or ``None``.

        Used to detect a client/server version skew, which silently breaks
        writes (external-array shapes get stored as 0, so images 500 on read).
        Degrades to ``None`` if unavailable rather than raising.
        """
        client = self._client
        if client is None:
            return None
        try:
            return client.context.server_info.library_version
        except Exception:
            return None

    @property
    def error_message(self) -> str:
        """Last error message if state is ERROR."""
        return self._error_message

    def configure(
        self,
        url: str,
        api_key: str | None = None,
        enabled: bool = True,
        auth_mode: TiledAuthMode | str = TiledAuthMode.NONE,
    ) -> None:
        """Configure the Tiled connection.

        If currently connected, will disconnect first.

        Args:
            url: Tiled server URL.
            api_key: Optional API key for authentication.
            enabled: Whether Tiled integration is enabled.
            auth_mode: Authentication mode (NONE, API_KEY, or KEYCLOAK).
        """
        # Disconnect if currently connected
        if self._state in (TiledConnectionState.CONNECTED, TiledConnectionState.CONNECTING):
            self.disconnect()

        # Convert string to enum if needed
        if isinstance(auth_mode, str):
            auth_mode = TiledAuthMode(auth_mode)

        self._config = TiledConfig(url=url, api_key=api_key, enabled=enabled, auth_mode=auth_mode)
        logger.info(
            "Tiled configured: url={}, enabled={}, auth_mode={}",
            url,
            enabled,
            auth_mode.value,
        )

    def connect(self) -> bool:
        """Connect to the Tiled server.

        Creates a Tiled client and subscribes TiledWriter to the Engine.

        Returns:
            True if connection successful.
        """
        if not self._config.enabled:
            logger.debug("Tiled not enabled, skipping connection")
            return False

        if not self._config.url:
            self._set_state(TiledConnectionState.ERROR, "No Tiled URL configured")
            return False

        self._set_state(TiledConnectionState.CONNECTING, "Connecting to Tiled server...")

        try:
            from tiled.client import from_uri

            # Set up authentication
            kwargs = {}
            if self._config.auth_mode == TiledAuthMode.API_KEY and self._config.api_key:
                kwargs["api_key"] = self._config.api_key
            elif self._config.auth_mode == TiledAuthMode.KEYCLOAK:
                from lightfall.auth.service_key_auth import ServiceKeyAuth

                kwargs["auth"] = ServiceKeyAuth("tiled")
                logger.debug("Using Keycloak authentication for Tiled (sync)")
            elif self._config.api_key:
                kwargs["api_key"] = self._config.api_key

            # Patch tiled Transport BEFORE from_uri (it connects immediately)
            proxy_url = self._get_proxy_url(self._config.url)
            restore_info = None
            if proxy_url:
                restore_info = self._patch_tiled_for_proxy(proxy_url)
            try:
                self._client = from_uri(self._config.url, **kwargs)
            finally:
                self._restore_tiled_transport(restore_info)

            # Verify connection by accessing context
            _ = self._client.context

            # Subscribe writer to engine
            self._subscribe_writer()

            # Start health check timer
            self._health_timer.start(self.HEALTH_CHECK_INTERVAL_MS)

            self._set_state(TiledConnectionState.CONNECTED, "Connected to Tiled server")
            logger.info("Connected to Tiled server: {}", self._config.url)
            return True

        except ImportError:
            self._set_state(
                TiledConnectionState.ERROR,
                "Tiled client not installed. Install with: pip install tiled[client]",
            )
            return False
        except Exception as e:
            self._set_state(TiledConnectionState.ERROR, f"Connection failed: {e}")
            logger.error("Failed to connect to Tiled: {}", e)
            return False

    def adopt_client(self, client: Any, url: str = "") -> None:
        """Adopt an externally-created, already-authenticated Tiled client.

        Used when an NSLS-II profile-collection run has already produced a
        Duo-authenticated, node-scoped reading client (e.g. ``mig`` →
        ``from_uri(...)['cms/migration']``). Reading through that client reuses
        the warmed token and inherits node scoping, so this skips ``from_uri``,
        auth-mode handling, and proxy patching entirely.

        Args:
            client: A ready Tiled client (already authenticated and scoped).
            url: Optional server URL, recorded for display/health only.
        """
        if self._state in (TiledConnectionState.CONNECTED, TiledConnectionState.CONNECTING):
            self.disconnect()

        self._config = TiledConfig(
            url=url, api_key=None, enabled=True, auth_mode=TiledAuthMode.NONE
        )
        self._client = client
        self._adopted = True
        self._set_state(TiledConnectionState.CONNECTED, "Adopted external Tiled client")
        # Reuse the existing periodic health check against the adopted client.
        self._health_timer.start(self.HEALTH_CHECK_INTERVAL_MS)
        logger.info("Tiled adopted external client (url={})", url or "<unspecified>")

    def connect_async(self) -> None:
        """Connect to the Tiled server asynchronously.

        The connection is established in a background thread to avoid
        blocking the UI. The connection_changed signal is emitted when
        the connection state changes.

        For KEYCLOAK auth mode, connection will only proceed if a user
        is authenticated.
        """
        if not self._config.enabled:
            logger.debug("Tiled not enabled, skipping connection")
            return

        if self._adopted:
            logger.debug("Skipping auto-connect: an external Tiled client was adopted")
            return

        if not self._config.url:
            self._set_state(TiledConnectionState.ERROR, "No Tiled URL configured")
            return

        if self._state == TiledConnectionState.CONNECTING:
            logger.debug("Connection already in progress")
            return

        # For Keycloak auth, check if user is authenticated
        if self._config.auth_mode == TiledAuthMode.KEYCLOAK:
            from lightfall.auth.session import AuthState, SessionManager

            session_manager = SessionManager.get_instance()
            if session_manager.state != AuthState.AUTHENTICATED:
                logger.debug("Keycloak auth mode: waiting for user authentication")
                self._set_state(
                    TiledConnectionState.DISCONNECTED,
                    "Waiting for authentication",
                )
                return

        self._set_state(TiledConnectionState.CONNECTING, "Connecting to Tiled server...")

        # Capture config for background thread
        url = self._config.url
        api_key = self._config.api_key
        auth_mode = self._config.auth_mode

        # Run connection in background thread
        self._connect_thread = QThreadFuture(
            self._do_connect,
            url,
            api_key,
            auth_mode,
            callback_slot=self._on_connect_complete,
            except_slot=self._on_connect_error,
            name="tiled_connect",
        )
        self._connect_thread.start()

    @staticmethod
    def _get_auth_headers() -> dict[str, str]:
        """Get Authorization headers for Tiled from the cached service API key.

        Auth-v2: returns the cached per-service API key minted at login as
        an ``Apikey <secret>`` Authorization header (Tiled's auth-v2 wire
        format), not a Keycloak bearer token.

        Returns:
            Dict with Authorization header, or empty dict if no key is cached.
        """
        try:
            from lightfall.auth.session import SessionManager

            secret = SessionManager.get_instance().get_api_key("tiled")
            if secret:
                # Tiled auth-v2 wire format: "Apikey <secret>"
                return {"Authorization": f"Apikey {secret}"}
        except Exception as e:
            logger.warning("Could not get Tiled API key: {}", e)
        return {}

    @staticmethod
    def _get_proxy_url(url: str) -> str | None:
        """Get the proxy URL for a given Tiled server URL.

        Args:
            url: The Tiled server URL.

        Returns:
            Proxy URL or None.
        """
        try:
            from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider

            proxy = ProxySettingsProvider.should_use_proxy_for_url(url)
            logger.info(
                "Tiled proxy lookup for {}: {}",
                url,
                proxy if proxy else "no proxy configured",
            )
            return proxy
        except Exception as e:
            logger.warning("Tiled proxy lookup failed: {}", e)
            return None

    @staticmethod
    def _test_proxy_connectivity(proxy_url: str, target_url: str) -> None:
        """Test that the proxy can actually reach the target URL.

        Args:
            proxy_url: The SOCKS/HTTP proxy URL.
            target_url: The target URL to test.
        """
        try:
            import httpx

            if proxy_url.startswith("socks"):
                from httpx_socks import SyncProxyTransport

                transport = SyncProxyTransport.from_url(proxy_url)
                with httpx.Client(transport=transport, timeout=10.0) as client:
                    resp = client.get(target_url)
                    logger.info(
                        "Proxy connectivity test OK: {} → {} (status {})",
                        proxy_url,
                        target_url,
                        resp.status_code,
                    )
            else:
                with httpx.Client(proxy=proxy_url, timeout=10.0) as client:
                    resp = client.get(target_url)
                    logger.info(
                        "Proxy connectivity test OK: {} → {} (status {})",
                        proxy_url,
                        target_url,
                        resp.status_code,
                    )
        except Exception as e:
            logger.error("Proxy connectivity test FAILED: {} → {}: {}", proxy_url, target_url, e)

    @staticmethod
    def _create_proxy_transport(proxy_url: str) -> Any:
        """Create a proxy-aware httpx transport.

        Args:
            proxy_url: The proxy URL (e.g. ``socks5://localhost:1080``).

        Returns:
            A transport instance, or None if creation failed.
        """
        if proxy_url.startswith("socks"):
            try:
                from httpx_socks import SyncProxyTransport

                t = SyncProxyTransport.from_url(proxy_url)
                logger.info("Created SyncProxyTransport for {}", proxy_url)
                return t
            except ImportError:
                logger.error(
                    "httpx-socks not installed — cannot use SOCKS proxy for Tiled. "
                    "Install with: pip install httpx-socks"
                )
                return None
        else:
            import httpx

            t = httpx.HTTPTransport(proxy=proxy_url)
            logger.info("Created HTTPTransport(proxy={}) for Tiled", proxy_url)
            return t

    @staticmethod
    def _patch_tiled_for_proxy(proxy_url: str) -> dict[str, Any] | None:
        """Patch tiled to use a SOCKS/HTTP proxy for all connections.

        Tiled's ``Context.from_any_uri`` makes a bare ``httpx.get()`` call
        (to check for HTTP→HTTPS redirects) before creating its transport.
        We must patch both:

        1. ``httpx.get`` / ``httpx.request`` — for the redirect check
        2. ``context.Transport`` — for the actual client connection

        Args:
            proxy_url: The proxy URL.

        Returns:
            Dict with restore info, or None if patching failed.
        """
        try:
            import httpx
            import tiled.client.context as context_mod
            from tiled.client.transport import Transport as OriginalTransport

            proxy_transport = TiledService._create_proxy_transport(proxy_url)
            if proxy_transport is None:
                return None

            # 1. Patch httpx.get (used by Context.from_any_uri for redirect check)
            #    Create a proxy-aware client for top-level httpx functions.
            original_httpx_get = httpx.get

            def proxy_httpx_get(url, **kwargs):
                logger.info("Intercepted httpx.get({}) — routing through proxy", url)
                # Create a one-shot client with proxy transport
                proxy_t = TiledService._create_proxy_transport(proxy_url)
                with httpx.Client(transport=proxy_t, timeout=15.0) as client:
                    return client.get(url, **kwargs)

            context_mod.httpx.get = proxy_httpx_get  # type: ignore[attr-defined]

            # 2. Patch Transport for the actual client
            class ProxyTransport(OriginalTransport):
                """Transport wrapper that uses a proxy-aware inner transport."""

                def __init__(self, *, transport=None, **kwargs):
                    logger.info("ProxyTransport.__init__: injecting proxy inner transport")
                    super().__init__(transport=proxy_transport, **kwargs)

            original_transport_cls = context_mod.Transport
            context_mod.Transport = ProxyTransport

            logger.info("Patched tiled for proxy: {}", proxy_url)

            return {
                "original_transport_cls": original_transport_cls,
                "original_httpx_get": original_httpx_get,
                "context_mod": context_mod,
            }
        except Exception as e:
            import traceback

            logger.error(
                "Failed to patch tiled for proxy: {}\n{}",
                e,
                traceback.format_exc(),
            )
            return None

    @staticmethod
    def _restore_tiled_transport(restore_info: dict[str, Any] | None) -> None:
        """Restore all tiled patches."""
        if restore_info is None:
            return
        try:
            context_mod = restore_info["context_mod"]
            context_mod.Transport = restore_info["original_transport_cls"]
            context_mod.httpx.get = restore_info["original_httpx_get"]
            logger.debug("Restored original tiled Transport and httpx.get")
        except Exception:
            pass

    def _do_connect(self, url: str, api_key: str | None, auth_mode: TiledAuthMode) -> Any:
        """Perform the actual connection (runs in background thread).

        Args:
            url: Tiled server URL.
            api_key: Optional API key.
            auth_mode: Authentication mode.

        Returns:
            The Tiled client if successful.
        """
        from tiled.client import from_uri

        kwargs: dict[str, Any] = {}

        if auth_mode == TiledAuthMode.API_KEY and api_key:
            kwargs["api_key"] = api_key
        elif auth_mode == TiledAuthMode.KEYCLOAK:
            from lightfall.auth.service_key_auth import ServiceKeyAuth

            kwargs["auth"] = ServiceKeyAuth("tiled")
            logger.debug("Using Keycloak authentication for Tiled")

        # Proxy setup
        proxy_url = self._get_proxy_url(url)
        restore_info = None
        if proxy_url:
            restore_info = self._patch_tiled_for_proxy(proxy_url)
        try:
            client = from_uri(url, **kwargs)
        finally:
            self._restore_tiled_transport(restore_info)

        # Verify connection by accessing context
        _ = client.context

        return client

    def _on_connect_complete(self, client: Any = None) -> None:
        """Handle successful connection (called in main thread).

        Args:
            client: The Tiled client instance.
        """
        # Ignore the second call with None when generator ends
        if client is None:
            return

        # An external client was adopted while this (auto) connect was in flight
        # -- e.g. the Keycloak auth handler started a connect to the configured
        # URL, which only completed AFTER the CMS bootstrap adopted its
        # write-scoped client. Keep the adopted client; don't clobber it.
        if self._adopted:
            logger.info(
                "Ignoring auto-connect result: external Tiled client adopted "
                "(keeping adopted client)"
            )
            return

        self._client = client

        # Subscribe writer to engine (must be done in main thread)
        self._subscribe_writer()

        # Start health check timer
        self._health_timer.start(self.HEALTH_CHECK_INTERVAL_MS)

        self._set_state(TiledConnectionState.CONNECTED, "Connected to Tiled server")
        logger.info("Connected to Tiled server: {}", self._config.url)

    def _on_connect_error(self, error: Exception) -> None:
        """Handle connection error (called in main thread).

        Args:
            error: The exception that occurred.
        """
        if isinstance(error, ImportError):
            self._set_state(
                TiledConnectionState.ERROR,
                "Tiled client not installed. Install with: pip install tiled[client]",
            )
        else:
            self._set_state(TiledConnectionState.ERROR, f"Connection failed: {error}")
            logger.error("Failed to connect to Tiled: {}", error)

            # Show a toast hint if the error looks like a SOCKS proxy failure
            if self._is_proxy_connection_error(error):
                try:
                    from lightfall.ui.toast import ToastManager

                    ToastManager.get_instance().warning(
                        "SOCKS proxy not reachable",
                        "Is your SSH tunnel or proxy running? "
                        "Check Preferences → Proxy to verify settings.",
                    )
                except Exception:
                    pass  # toast system not available yet

    @staticmethod
    def _is_proxy_connection_error(error: Exception) -> bool:
        """Check whether an error indicates a SOCKS proxy connection failure."""
        # Walk the exception chain (cause / context)
        exc: BaseException | None = error
        while exc is not None:
            type_name = type(exc).__name__
            if "ProxyConnectionError" in type_name or "ProxyError" in type_name:
                return True
            msg = str(exc).lower()
            if "could not connect to proxy" in msg or "proxy connection" in msg:
                return True
            exc = exc.__cause__ or exc.__context__
        return False

    def disconnect(self) -> None:
        """Disconnect from the Tiled server.

        Unsubscribes TiledWriter from Engine and cleans up resources.
        """
        # Stop health check timer
        self._health_timer.stop()

        # Unsubscribe writer
        self._unsubscribe_writer()

        # Clear client
        self._client = None
        self._adopted = False

        self._set_state(TiledConnectionState.DISCONNECTED, "Disconnected from Tiled server")
        logger.info("Disconnected from Tiled server")

    def test_connection(
        self,
        url: str,
        api_key: str | None = None,
        auth_mode: str | None = None,
    ) -> tuple[bool, str]:
        """Test connection to a Tiled server without modifying state.

        Args:
            url: Tiled server URL to test.
            api_key: Optional API key.
            auth_mode: Authentication mode ("none", "api_key", "keycloak").

        Returns:
            Tuple of (success, message).
        """
        if not url:
            return False, "No URL provided"

        try:
            from tiled.client import from_uri

            kwargs = {}
            if auth_mode == "keycloak":
                from lightfall.auth.service_key_auth import ServiceKeyAuth

                # Check that we have a token before attempting
                headers = self._get_auth_headers()
                if not headers:
                    return False, "Not authenticated — log in to Keycloak first"
                kwargs["auth"] = ServiceKeyAuth("tiled")
            elif api_key:
                kwargs["api_key"] = api_key

            proxy_url = TiledService._get_proxy_url(url)
            restore_info = None
            if proxy_url:
                restore_info = TiledService._patch_tiled_for_proxy(proxy_url)
            try:
                client = from_uri(url, **kwargs)
            finally:
                TiledService._restore_tiled_transport(restore_info)
            # Verify connection
            _ = client.context
            return True, "Connection successful"

        except ImportError:
            return False, "Tiled client not installed"
        except Exception as e:
            return False, f"Connection failed: {e}"

    def _subscribe_writer(self) -> None:
        """Subscribe TiledWriter to Engine for document streaming.

        Uses ThreadedTiledWriter wrapper to prevent blocking the main thread
        during HTTP calls to the Tiled server.
        """
        if self._client is None:
            return

        try:
            from lightfall.acquire import get_engine
            from lightfall.services.threaded_tiled_writer import ThreadedTiledWriter

            engine = get_engine()

            raw_writer = TiledWriter(self._client, batch_size=1, max_array_size=0)

            # Wrap in ThreadedTiledWriter to prevent blocking
            self._writer = ThreadedTiledWriter(
                raw_writer,
                error_callback=self._on_writer_error,
            )
            self._subscription_token = engine.subscribe(self._writer)
            logger.debug("ThreadedTiledWriter subscribed to Engine")

            # Install AccessStamper if beamline + alshub prefs are configured
            try:
                from lightfall.auth.session import SessionManager
                from lightfall.services._alshub_client import AlshubClient
                from lightfall.services.access_stamper import AccessStamper, install_into_run_engine
                from lightfall.ui.preferences.manager import PreferencesManager
                from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider

                prefs = PreferencesManager.get_instance()
                beamline = prefs.get("tiled_beamline", None) or None
                alshub_url = prefs.get("tiled_alshub_url", None) or None
                # Honor Lightfall's shared Network Proxy settings (global on/off
                # plus auto-detect for *.lbl.gov). Returns None when no proxy
                # is configured for this URL.
                alshub_proxy = (
                    ProxySettingsProvider.should_use_proxy_for_url(alshub_url)
                    if alshub_url else None
                )
                if beamline and alshub_url:
                    # `engine` here is Lightfall's BaseEngine wrapper. The bluesky
                    # RunEngine that actually executes plans (and reads
                    # `.preprocessors`) lives at `engine.RE`. Installing on the
                    # wrapper would silently no-op because `wrapper(plan)` calls
                    # `self._RE(plan, **kwargs)` directly — it does not
                    # consult `wrapper.preprocessors`.
                    re = getattr(engine, "RE", None)
                    if re is None:
                        logger.warning(
                            "AccessStamper not installed: engine.RE is None "
                            "(engine wrapper not fully initialized yet)"
                        )
                    else:
                        # active-esaf is a public route on alshub-api; no key needed.
                        stamper = AccessStamper(
                            beamline=beamline,
                            alshub_client=AlshubClient(
                                base_url=alshub_url, proxy=alshub_proxy
                            ),
                            session_provider=lambda: SessionManager.get_instance().session,
                            settings_provider=lambda: _SettingsAdapter(),
                        )
                        install_into_run_engine(stamper, re)
                        logger.info(
                            "AccessStamper installed for beamline={} (proxy={})",
                            beamline,
                            alshub_proxy or "none",
                        )
                else:
                    logger.debug(
                        "AccessStamper not installed: tiled_beamline and/or tiled_alshub_url not configured"
                    )
            except Exception as e:
                logger.warning("Failed to install AccessStamper: {}", e)

        except Exception as e:
            logger.error("Failed to subscribe TiledWriter: {}", e)

    def _on_writer_error(
        self, name: str, doc: dict[str, Any], error: Exception
    ) -> None:
        """Handle errors from the threaded writer.

        Args:
            name: Document name that failed.
            doc: Document that failed.
            error: The exception that occurred.
        """
        # Check if it's a permissions error
        error_str = str(error)
        if "401" in error_str or "Not enough permissions" in error_str:
            # Only log once per session to avoid spam
            if not hasattr(self, "_permission_error_logged"):
                self._permission_error_logged = True
                logger.warning(
                    "Tiled write permission denied. Data will not be saved to Tiled. "
                    "Configure an API key with write permissions or disable Tiled."
                )

    def _unsubscribe_writer(self) -> None:
        """Unsubscribe TiledWriter from Engine and flush pending documents."""
        if self._subscription_token is not None:
            try:
                from lightfall.acquire import get_engine

                engine = get_engine()
                engine.unsubscribe(self._subscription_token)
                logger.debug("TiledWriter unsubscribed from Engine")
            except Exception as e:
                logger.warning("Failed to unsubscribe TiledWriter: {}", e)

        # Flush and stop the threaded writer to ensure all documents
        # (especially stop docs) are written before we discard it
        if self._writer is not None:
            try:
                self._writer.flush(timeout=10.0)
                self._writer.stop(timeout=5.0)
                logger.debug("ThreadedTiledWriter flushed and stopped")
            except Exception as e:
                logger.warning("Failed to flush/stop ThreadedTiledWriter: {}", e)

        self._writer = None
        self._subscription_token = None

    def _health_check(self) -> None:
        """Perform health check on Tiled connection."""
        if self._client is None:
            return

        try:
            # Access context to verify connection
            _ = self._client.context
        except Exception as e:
            logger.warning("Tiled health check failed: {}", e)
            self._set_state(TiledConnectionState.ERROR, f"Connection lost: {e}")
            # Stop health checks until reconnected
            self._health_timer.stop()
            # Clean up writer
            self._unsubscribe_writer()
            self._client = None

    def _set_state(self, state: TiledConnectionState, message: str) -> None:
        """Set connection state and emit signal.

        Args:
            state: New connection state.
            message: Status message.
        """
        old_state = self._state
        self._state = state

        if state == TiledConnectionState.ERROR:
            self._error_message = message
        else:
            self._error_message = ""

        if old_state != state:
            logger.debug("Tiled state: {} -> {} ({})", old_state.value, state.value, message)
            self.connection_changed.emit(state, message)

    def get_status_info(self) -> dict[str, Any]:
        """Get status information for display.

        Returns:
            Dictionary with status information.
        """
        return {
            "state": self._state.value,
            "enabled": self._config.enabled,
            "url": self._config.url,
            "connected": self.is_connected,
            "error": self._error_message,
            "has_writer": self._writer is not None,
            "auth_mode": self._config.auth_mode.value,
        }

    def connect_session_manager(self) -> None:
        """Connect to SessionManager signals for Keycloak auth mode.

        When using KEYCLOAK auth mode, this connects to SessionManager
        signals to automatically connect/disconnect when the user
        logs in/out.
        """
        if self._session_connected:
            return

        from lightfall.auth.session import SessionManager

        session_manager = SessionManager.get_instance()
        session_manager.state_changed.connect(self._on_auth_state_changed)
        self._session_connected = True
        logger.debug("TiledService connected to SessionManager")

    def _on_auth_state_changed(self, new_state: Any, old_state: Any) -> None:
        """Handle authentication state changes.

        Args:
            new_state: New AuthState.
            old_state: Previous AuthState.
        """
        from lightfall.auth.session import AuthState

        if self._config.auth_mode != TiledAuthMode.KEYCLOAK:
            return

        if new_state == AuthState.AUTHENTICATED:
            # User logged in - connect to Tiled
            logger.info("User authenticated, connecting to Tiled")
            self.connect_async()
        elif old_state == AuthState.AUTHENTICATED:
            # User logged out - disconnect from Tiled
            logger.info("User logged out, disconnecting from Tiled")
            self.disconnect()


def _install_tiled_stream_ws_proxy_patch() -> None:
    """Install a process-global monkey-patch on tiled.client.stream.connect.

    Tiled's stream Subscription._connect uses websockets.sync.client.connect,
    which doesn't honor SOCKS proxies (it only supports HTTP-CONNECT proxies
    via env vars). Lightfall's REST path goes through SOCKS via
    TiledService._patch_tiled_for_proxy, so without this WS-side patch a user
    outside the LBL network sees REST work and WS time out — which leaks
    Tiled-issued ephemeral apikeys (each WS attempt mints one and revokes
    only on success).

    The patch is installed once at module import; the wrapper does a live
    proxy lookup via ProxySettingsProvider on every call, so it's a no-op
    when no proxy is configured and adapts to runtime preference changes.

    Idempotent — safe to call multiple times. Subsequent calls do nothing.
    """
    import tiled.client.stream as stream_mod

    # Idempotency guard: if our wrapper is already installed, skip.
    if getattr(stream_mod.connect, "_lightfall_socks_patched", False):
        return

    original_stream_connect = stream_mod.connect

    def proxy_ws_connect(uri, **kwargs):
        # Live lookup — no stale proxy_url captured in this closure.
        try:
            from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider
            proxy_url = ProxySettingsProvider.should_use_proxy_for_url(uri)
        except Exception as e:
            logger.warning("WS proxy lookup failed for {}: {}", uri, e)
            proxy_url = None

        if proxy_url and proxy_url.startswith("socks"):
            from urllib.parse import urlparse
            parsed = urlparse(uri)
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "wss" else 80)
            try:
                from python_socks.sync import Proxy
                sock = Proxy.from_url(proxy_url).connect(host, port)
            except Exception as exc:
                logger.error(
                    "SOCKS connect for WS {} failed: {}", uri, exc
                )
                raise
            kwargs.setdefault("sock", sock)
            kwargs.setdefault("server_hostname", host)
            if parsed.scheme == "wss":
                import ssl
                kwargs.setdefault("ssl", ssl.create_default_context())
        # Non-SOCKS or no-proxy cases: delegate; websockets handles HTTP
        # CONNECT proxies via its own proxy= default when HTTPS_PROXY is set.
        return original_stream_connect(uri, **kwargs)

    # Mark the wrapper so subsequent imports don't double-patch.
    proxy_ws_connect._lightfall_socks_patched = True
    stream_mod.connect = proxy_ws_connect
    logger.debug("Installed tiled.client.stream.connect SOCKS-proxy wrapper")


# Eagerly install at module import time so any Tiled stream connection in
# this process honors Lightfall's proxy configuration regardless of when it fires.
_install_tiled_stream_ws_proxy_patch()
