"""Device connection manager for background ophyd instantiation.

Provides non-blocking device connection with configurable timeouts,
using Qt threading utilities for proper UI integration.
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from PySide6.QtCore import QObject, Signal

from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

if TYPE_CHECKING:
    from lightfall.devices.model import DeviceInfo


class ConnectionState(StrEnum):
    """Connection state for a device."""

    PENDING = "pending"  # Not yet attempted
    CONNECTING = "connecting"  # Connection in progress
    CONNECTED = "connected"  # Successfully connected
    FAILED = "failed"  # Connection failed
    TIMEOUT = "timeout"  # Connection timed out


@dataclass
class ConnectionResult:
    """Result of a device connection attempt."""

    device_id: UUID
    device_name: str
    state: ConnectionState
    ophyd_device: Any | None = None
    error: str | None = None
    elapsed_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


class DeviceConnectionManager(QObject):
    """Singleton manager for background device connections.

    Handles ophyd device instantiation in background threads with
    configurable timeouts. Emits Qt signals for connection status
    updates that can be connected to UI components.

    Signals:
        device_connecting: Emitted when connection starts (device_id: str)
        device_connected: Emitted on success (ConnectionResult)
        device_failed: Emitted on failure (ConnectionResult)
        all_connections_complete: Emitted when a batch finishes

    Example:
        manager = DeviceConnectionManager.get_instance()
        manager.device_connected.connect(on_device_ready)
        manager.connect_device(device_info, happi_result)
    """

    _instance: ClassVar[DeviceConnectionManager | None] = None
    _lock = threading.Lock()

    # Signals
    device_connecting = Signal(str)  # device_id
    device_connected = Signal(object)  # ConnectionResult
    device_failed = Signal(object)  # ConnectionResult
    all_connections_complete = Signal()

    # Default timeout in seconds
    DEFAULT_TIMEOUT: ClassVar[float] = 5.0

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the connection manager."""
        super().__init__(parent)

        # Global default timeout
        self._default_timeout: float = self.DEFAULT_TIMEOUT

        # Per-device timeout overrides (device_id -> timeout_seconds)
        self._device_timeouts: dict[UUID, float] = {}

        # Connection state tracking
        self._connection_states: dict[UUID, ConnectionState] = {}
        self._connection_results: dict[UUID, ConnectionResult] = {}

        # Active connection threads
        self._active_threads: dict[UUID, QThreadFuture] = {}

        # Pending batch tracking
        self._pending_count = 0
        self._pending_lock = threading.Lock()

        # Whether to connect on startup
        self._connect_on_startup: bool = True

    @classmethod
    def get_instance(cls) -> DeviceConnectionManager:
        """Get the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.cancel_all()
            cls._instance = None

    # === Configuration ===

    @property
    def default_timeout(self) -> float:
        """Get the default connection timeout in seconds."""
        return self._default_timeout

    @default_timeout.setter
    def default_timeout(self, value: float) -> None:
        """Set the default connection timeout in seconds."""
        self._default_timeout = max(0.5, min(value, 120.0))
        logger.debug("Default connection timeout set to {}s", self._default_timeout)

    @property
    def connect_on_startup(self) -> bool:
        """Whether to connect devices on startup."""
        return self._connect_on_startup

    @connect_on_startup.setter
    def connect_on_startup(self, value: bool) -> None:
        """Set whether to connect devices on startup."""
        self._connect_on_startup = value

    def set_device_timeout(self, device_id: UUID, timeout: float | None) -> None:
        """Set a per-device timeout override.

        Args:
            device_id: The device ID.
            timeout: Timeout in seconds, or None to clear override.
        """
        if timeout is None:
            self._device_timeouts.pop(device_id, None)
        else:
            self._device_timeouts[device_id] = max(0.5, min(timeout, 120.0))

    def get_device_timeout(self, device_id: UUID) -> float:
        """Get the effective timeout for a device.

        Args:
            device_id: The device ID.

        Returns:
            Timeout in seconds (per-device override or default).
        """
        return self._device_timeouts.get(device_id, self._default_timeout)

    def load_settings(self) -> None:
        """Load settings from PreferencesManager."""
        try:
            from lightfall.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
            self._default_timeout = prefs.get("device_connection_timeout", self.DEFAULT_TIMEOUT)
            self._connect_on_startup = prefs.get("device_connect_on_startup", True)
            logger.debug(
                "Loaded connection settings: timeout={}s, connect_on_startup={}",
                self._default_timeout,
                self._connect_on_startup,
            )
        except Exception as e:
            logger.warning("Failed to load connection settings: {}", e)

    def save_settings(self) -> None:
        """Save settings to PreferencesManager."""
        try:
            from lightfall.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
            prefs.set("device_connection_timeout", self._default_timeout)
            prefs.set("device_connect_on_startup", self._connect_on_startup)
        except Exception as e:
            logger.warning("Failed to save connection settings: {}", e)

    # === Connection State ===

    def get_state(self, device_id: UUID) -> ConnectionState:
        """Get the connection state for a device."""
        return self._connection_states.get(device_id, ConnectionState.PENDING)

    def get_result(self, device_id: UUID) -> ConnectionResult | None:
        """Get the connection result for a device."""
        return self._connection_results.get(device_id)

    def is_connecting(self, device_id: UUID) -> bool:
        """Check if a device is currently connecting."""
        return self._connection_states.get(device_id) == ConnectionState.CONNECTING

    def is_connected(self, device_id: UUID) -> bool:
        """Check if a device is connected."""
        return self._connection_states.get(device_id) == ConnectionState.CONNECTED

    # === Connection Methods ===

    def connect_device(
        self,
        device_info: DeviceInfo,
        happi_result: Any,
        timeout: float | None = None,
    ) -> None:
        """Queue a device for background connection.

        Args:
            device_info: The DeviceInfo to connect.
            happi_result: The happi SearchResult (has .get() method).
            timeout: Optional timeout override in seconds.
        """
        device_id = device_info.id
        device_name = device_info.name

        # Skip if already connected or connecting
        current_state = self._connection_states.get(device_id)
        if current_state == ConnectionState.CONNECTED:
            logger.debug("Device '{}' already connected, skipping", device_name)
            return
        if current_state == ConnectionState.CONNECTING:
            logger.debug("Device '{}' already connecting, skipping", device_name)
            return

        # Determine timeout
        effective_timeout = timeout or self.get_device_timeout(device_id)

        # Update state
        self._connection_states[device_id] = ConnectionState.CONNECTING
        self.device_connecting.emit(str(device_id))

        # Track pending
        with self._pending_lock:
            self._pending_count += 1

        # Create background thread
        thread = QThreadFuture(
            self._do_connect,
            device_info,
            happi_result,
            effective_timeout,
            callback_slot=self._on_connection_complete,
            except_slot=self._on_connection_error,
            name=f"connect_{device_name}",
            key=f"device_connect_{device_id}",
        )

        self._active_threads[device_id] = thread
        thread.start()

        logger.debug(
            "Started background connection for '{}' (timeout={}s)",
            device_name,
            effective_timeout,
        )

    def connect_devices(
        self,
        backend: Any,
        infos: list[DeviceInfo],
        timeout: float | None = None,
        max_concurrency: int = 12,
    ) -> None:
        """Connect a list of devices using a backend, with bounded concurrency.

        Must be called from the main (GUI) thread. At most ``max_concurrency``
        per-device worker threads run at once; the rest are queued and started
        as each slot becomes free.

        Args:
            backend: A :class:`~lightfall.devices.base.DeviceBackend` whose
                ``instantiate`` and ``check_connection`` hooks are used.
            infos: Devices to connect.
            timeout: Per-device timeout in seconds. ``None`` uses the manager
                default (``self.default_timeout``).
            max_concurrency: Maximum number of simultaneous worker threads.
        """
        if not infos:
            return

        effective_timeout = timeout if timeout is not None else self.default_timeout

        # Pending queue of (info, backend, timeout) yet to be started.
        pending: collections.deque[DeviceInfo] = collections.deque(infos)

        # Track how many workers are currently in flight (only accessed from
        # the main thread via callback_slot, so no lock is needed).
        in_flight: list[int] = [0]  # mutable cell — avoids nonlocal in older Pythons

        def _start_next() -> None:
            """Pull from the pending queue and start a worker if a slot is free."""
            while in_flight[0] < max_concurrency and pending:
                info = pending.popleft()
                self._connection_states[info.id] = ConnectionState.CONNECTING
                self.device_connecting.emit(str(info.id))
                in_flight[0] += 1

                def _make_callback(captured_info: DeviceInfo):
                    def _on_done(result: ConnectionResult) -> None:
                        # Runs on the main thread (Qt signal marshalling).
                        self._connection_states[result.device_id] = result.state
                        self._connection_results[result.device_id] = result

                        if result.state == ConnectionState.CONNECTED:
                            captured_info._ophyd_device = result.ophyd_device
                            self.device_connected.emit(result)
                        else:
                            self.device_failed.emit(result)

                        in_flight[0] -= 1
                        _start_next()

                    return _on_done

                def _make_error_handler(captured_info: DeviceInfo):
                    def _on_error(exc: Exception) -> None:
                        # Runs on the main thread.
                        logger.error(
                            "connect_devices: unexpected error for '{}': {}",
                            captured_info.name,
                            exc,
                        )
                        fail_result = ConnectionResult(
                            device_id=captured_info.id,
                            device_name=captured_info.name,
                            state=ConnectionState.FAILED,
                            error=str(exc),
                        )
                        self._connection_states[captured_info.id] = ConnectionState.FAILED
                        self._connection_results[captured_info.id] = fail_result
                        self.device_failed.emit(fail_result)
                        in_flight[0] -= 1
                        _start_next()

                    return _on_error

                thread = QThreadFuture(
                    self._instantiate_and_connect,
                    backend,
                    info,
                    effective_timeout,
                    callback_slot=_make_callback(info),
                    except_slot=_make_error_handler(info),
                    name=f"connect_{info.name}",
                    key=f"device_connect_batch_{info.id}",
                )
                self._active_threads[info.id] = thread
                thread.start()

        _start_next()

    def _instantiate_and_connect(
        self,
        backend: Any,
        info: DeviceInfo,
        timeout: float,
    ) -> ConnectionResult:
        """Worker: instantiate then check connection for one device.

        Runs in a background thread. Returns a :class:`ConnectionResult` —
        never raises, so the ``except_slot`` only fires on truly unexpected
        errors outside this try/except.

        Args:
            backend: The device backend.
            info: Device to instantiate and connect.
            timeout: Connection timeout in seconds.

        Returns:
            :class:`ConnectionResult` with the final state.
        """
        start = time.monotonic()
        try:
            obj = backend.instantiate(info)
            ok = backend.check_connection(obj, timeout)
            elapsed = (time.monotonic() - start) * 1000
            state = ConnectionState.CONNECTED if ok else ConnectionState.TIMEOUT
            return ConnectionResult(
                device_id=info.id,
                device_name=info.name,
                state=state,
                ophyd_device=obj if ok else None,
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.warning(
                "connect_devices: '{}' failed: {}",
                info.name,
                exc,
            )
            return ConnectionResult(
                device_id=info.id,
                device_name=info.name,
                state=ConnectionState.FAILED,
                error=str(exc),
                elapsed_ms=elapsed,
            )

    def retry_connection(
        self,
        device_info: DeviceInfo,
        happi_result: Any,
        timeout: float | None = None,
    ) -> None:
        """Retry a failed connection.

        Clears previous state and attempts connection again.

        Args:
            device_info: The DeviceInfo to connect.
            happi_result: The happi SearchResult.
            timeout: Optional timeout override.
        """
        device_id = device_info.id

        # Clear previous state
        self._connection_states.pop(device_id, None)
        self._connection_results.pop(device_id, None)

        # Cancel any active thread
        if device_id in self._active_threads:
            thread = self._active_threads.pop(device_id)
            if thread.running:
                thread.cancel()

        # Start fresh connection
        self.connect_device(device_info, happi_result, timeout)

    def cancel_connection(self, device_id: UUID) -> None:
        """Cancel an in-progress connection."""
        if device_id in self._active_threads:
            thread = self._active_threads.pop(device_id)
            if thread.running:
                thread.cancel()
                logger.debug("Cancelled connection for device {}", device_id)

        self._connection_states[device_id] = ConnectionState.FAILED

    def cancel_all(self) -> None:
        """Cancel all in-progress connections."""
        for device_id in list(self._active_threads.keys()):
            self.cancel_connection(device_id)

    # === Internal Methods ===

    def _do_connect(
        self,
        device_info: DeviceInfo,
        happi_result: Any,
        timeout: float,
    ) -> ConnectionResult:
        """Perform the actual connection (runs in background thread).

        Args:
            device_info: The DeviceInfo to connect.
            happi_result: The happi SearchResult with .get() method.
            timeout: Connection timeout in seconds.

        Returns:
            ConnectionResult with success/failure info.
        """
        device_id = device_info.id
        device_name = device_info.name
        start_time = time.monotonic()

        try:
            # Get the ophyd device from happi
            if not hasattr(happi_result, "get"):
                raise ValueError("happi_result has no get() method")

            ophyd_device = happi_result.get()

            if ophyd_device is None:
                raise ValueError("happi_result.get() returned None")

            # Wait for connection with timeout
            if hasattr(ophyd_device, "wait_for_connection"):
                ophyd_device.wait_for_connection(timeout=timeout)
            elif hasattr(ophyd_device, "connected"):
                # Poll for connection
                deadline = time.monotonic() + timeout
                while not ophyd_device.connected and time.monotonic() < deadline:
                    time.sleep(0.1)
                if not ophyd_device.connected:
                    raise TimeoutError(f"Device did not connect within {timeout}s")

            elapsed = (time.monotonic() - start_time) * 1000

            logger.info(
                "Device '{}' connected in {:.1f}ms",
                device_name,
                elapsed,
            )

            return ConnectionResult(
                device_id=device_id,
                device_name=device_name,
                state=ConnectionState.CONNECTED,
                ophyd_device=ophyd_device,
                elapsed_ms=elapsed,
            )

        except TimeoutError as e:
            elapsed = (time.monotonic() - start_time) * 1000
            logger.warning(
                "Device '{}' connection timed out after {:.1f}ms: {}",
                device_name,
                elapsed,
                e,
            )
            return ConnectionResult(
                device_id=device_id,
                device_name=device_name,
                state=ConnectionState.TIMEOUT,
                error=str(e),
                elapsed_ms=elapsed,
            )

        except Exception as e:
            elapsed = (time.monotonic() - start_time) * 1000
            logger.warning(
                "Device '{}' connection failed after {:.1f}ms: {}",
                device_name,
                elapsed,
                e,
            )
            return ConnectionResult(
                device_id=device_id,
                device_name=device_name,
                state=ConnectionState.FAILED,
                error=str(e),
                elapsed_ms=elapsed,
            )

    def _on_connection_complete(self, result: ConnectionResult) -> None:
        """Handle successful connection result (called in main thread)."""
        device_id = result.device_id

        # Clean up thread reference
        self._active_threads.pop(device_id, None)

        # Update state
        self._connection_states[device_id] = result.state
        self._connection_results[device_id] = result

        # Emit appropriate signal
        if result.state == ConnectionState.CONNECTED:
            self.device_connected.emit(result)
        else:
            self.device_failed.emit(result)

        # Check if batch complete
        self._check_batch_complete()

    def _on_connection_error(self, error: Exception) -> None:
        """Handle connection thread error (called in main thread)."""
        logger.error("Unexpected connection error: {}", error)
        self._check_batch_complete()

    def _check_batch_complete(self) -> None:
        """Check if all pending connections are complete."""
        with self._pending_lock:
            self._pending_count -= 1
            if self._pending_count <= 0:
                self._pending_count = 0
                self.all_connections_complete.emit()

    # === Introspection ===

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        return {
            "default_timeout": self._default_timeout,
            "connect_on_startup": self._connect_on_startup,
            "active_connections": len(self._active_threads),
            "pending_count": self._pending_count,
            "states": {str(did): state.value for did, state in self._connection_states.items()},
            "device_timeouts": {
                str(did): timeout for did, timeout in self._device_timeouts.items()
            },
        }
