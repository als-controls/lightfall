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

from lucid.utils.logging import logger
from lucid.utils.threads import QThreadFuture

if TYPE_CHECKING:
    pass


class TiledConnectionState(Enum):
    """Connection state for the Tiled service."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class TiledConfig:
    """Configuration for Tiled connection."""

    url: str = ""
    api_key: str | None = None
    enabled: bool = False


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
        self._writer: Any = None
        self._subscription_token: int | None = None
        self._error_message: str = ""
        self._connect_thread: QThreadFuture | None = None

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
    def error_message(self) -> str:
        """Last error message if state is ERROR."""
        return self._error_message

    def configure(
        self,
        url: str,
        api_key: str | None = None,
        enabled: bool = True,
    ) -> None:
        """Configure the Tiled connection.

        If currently connected, will disconnect first.

        Args:
            url: Tiled server URL.
            api_key: Optional API key for authentication.
            enabled: Whether Tiled integration is enabled.
        """
        # Disconnect if currently connected
        if self._state in (TiledConnectionState.CONNECTED, TiledConnectionState.CONNECTING):
            self.disconnect()

        self._config = TiledConfig(url=url, api_key=api_key, enabled=enabled)
        logger.info("Tiled configured: url={}, enabled={}", url, enabled)

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

            # Create client with optional API key
            kwargs = {}
            if self._config.api_key:
                kwargs["api_key"] = self._config.api_key

            self._client = from_uri(self._config.url, **kwargs)

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

    def connect_async(self) -> None:
        """Connect to the Tiled server asynchronously.

        The connection is established in a background thread to avoid
        blocking the UI. The connection_changed signal is emitted when
        the connection state changes.
        """
        if not self._config.enabled:
            logger.debug("Tiled not enabled, skipping connection")
            return

        if not self._config.url:
            self._set_state(TiledConnectionState.ERROR, "No Tiled URL configured")
            return

        if self._state == TiledConnectionState.CONNECTING:
            logger.debug("Connection already in progress")
            return

        self._set_state(TiledConnectionState.CONNECTING, "Connecting to Tiled server...")

        # Capture config for background thread
        url = self._config.url
        api_key = self._config.api_key

        # Run connection in background thread
        self._connect_thread = QThreadFuture(
            self._do_connect,
            url,
            api_key,
            callback_slot=self._on_connect_complete,
            except_slot=self._on_connect_error,
            name="tiled_connect",
        )
        self._connect_thread.start()

    def _do_connect(self, url: str, api_key: str | None) -> Any:
        """Perform the actual connection (runs in background thread).

        Args:
            url: Tiled server URL.
            api_key: Optional API key.

        Returns:
            The Tiled client if successful.
        """
        from tiled.client import from_uri

        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key

        client = from_uri(url, **kwargs)

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

        self._set_state(TiledConnectionState.DISCONNECTED, "Disconnected from Tiled server")
        logger.info("Disconnected from Tiled server")

    def test_connection(self, url: str, api_key: str | None = None) -> tuple[bool, str]:
        """Test connection to a Tiled server without modifying state.

        Args:
            url: Tiled server URL to test.
            api_key: Optional API key.

        Returns:
            Tuple of (success, message).
        """
        if not url:
            return False, "No URL provided"

        try:
            from tiled.client import from_uri

            kwargs = {}
            if api_key:
                kwargs["api_key"] = api_key

            client = from_uri(url, **kwargs)
            # Verify connection
            _ = client.context
            return True, "Connection successful"

        except ImportError:
            return False, "Tiled client not installed"
        except Exception as e:
            return False, f"Connection failed: {e}"

    def _subscribe_writer(self) -> None:
        """Subscribe TiledWriter to Engine for document streaming."""
        if self._client is None:
            return

        try:
            from bluesky.callbacks.tiled_writer import TiledWriter

            from lucid.acquire import get_engine

            engine = get_engine()
            self._writer = TiledWriter(self._client)
            self._subscription_token = engine.subscribe(self._writer)
            logger.debug("TiledWriter subscribed to Engine")

        except ImportError as e:
            logger.warning("Could not import TiledWriter: {}", e)
        except Exception as e:
            logger.error("Failed to subscribe TiledWriter: {}", e)

    def _unsubscribe_writer(self) -> None:
        """Unsubscribe TiledWriter from Engine."""
        if self._subscription_token is not None:
            try:
                from lucid.acquire import get_engine

                engine = get_engine()
                engine.unsubscribe(self._subscription_token)
                logger.debug("TiledWriter unsubscribed from Engine")
            except Exception as e:
                logger.warning("Failed to unsubscribe TiledWriter: {}", e)

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
        }
