"""
PV wrapper that bridges caproto to Qt signals.

Provides a Qt-friendly interface to EPICS PVs with signals for value changes.
"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot

from lucid.utils.crash_diagnostics import gui_thread_only


class PV(QObject):
    """
    A Qt-aware wrapper around a caproto PV.

    Emits Qt signals when the PV value changes, making it easy to connect
    to widget slots for automatic UI updates.

    Attributes:
        pv_name: The EPICS PV name this object represents.
        connected: Whether the PV is currently connected.
        value: The current PV value.

    Signals:
        value_changed: Emitted when the PV value changes. Carries the new value.
        connection_changed: Emitted when connection state changes. Carries bool.
        metadata_changed: Emitted when PV metadata changes (units, limits, etc).

    Example:
        >>> pv = PV("MY:PV:NAME")
        >>> pv.value_changed.connect(my_label.setText)
        >>> pv.connect()
    """

    value_changed = Signal(object)
    connection_changed = Signal(bool)
    metadata_changed = Signal(dict)

    # Internal signals for thread-safe updates from caproto callbacks
    _value_received = Signal(object)
    _connection_ready = Signal(bool)

    def __init__(
        self,
        pv_name: str,
        parent: QObject | None = None,
        auto_connect: bool = False,
    ) -> None:
        """
        Initialize a PV wrapper.

        Args:
            pv_name: The EPICS PV name to connect to.
            parent: Optional Qt parent object.
            auto_connect: If True, connect immediately on creation.
        """
        super().__init__(parent)
        self._pv_name = pv_name
        self._connected = False
        self._value: Any = None
        self._metadata: dict[str, Any] = {}
        self._caproto_pv = None
        self._subscription = None

        # Connect internal signals for thread-safe updates
        self._value_received.connect(self._handle_value_received, Qt.ConnectionType.QueuedConnection)
        self._connection_ready.connect(self._handle_connection_ready, Qt.ConnectionType.QueuedConnection)

        if auto_connect:
            self.connect_pv()

    @property
    def pv_name(self) -> str:
        """The EPICS PV name."""
        return self._pv_name

    @property
    def connected(self) -> bool:
        """Whether the PV is currently connected."""
        return self._connected

    @property
    def value(self) -> Any:
        """The current PV value."""
        return self._value

    @property
    def metadata(self) -> dict[str, Any]:
        """
        PV metadata including units, limits, precision, enum strings, etc.

        Returns:
            Dictionary with keys like 'units', 'lower_limit', 'upper_limit',
            'precision', 'enum_strings', etc. depending on the PV type.
        """
        return self._metadata.copy()

    def connect_pv(self) -> None:
        """
        Establish connection to the PV and start monitoring.

        This method is safe to call multiple times - it will only connect once.
        Connection is performed in a background thread to avoid blocking the GUI.
        """
        if self._caproto_pv is not None:
            return

        # Start connection in background thread to avoid blocking
        thread = threading.Thread(target=self._connect_pv_blocking, daemon=True)
        thread.start()

    def _connect_pv_blocking(self) -> None:
        """
        Blocking connection logic - runs in background thread.
        """
        from lucid.epics.ca.context import SharedContext

        ctx = SharedContext.get_instance()
        self._caproto_pv = ctx.get_pv(self._pv_name)

        # Wait for connection with timeout
        try:
            self._caproto_pv.wait_for_connection(timeout=5.0)
            # Signal main thread that connection succeeded
            self._connection_ready.emit(True)
        except TimeoutError:
            self._connection_ready.emit(False)

    @Slot(bool)
    @gui_thread_only
    def _handle_connection_ready(self, connected: bool) -> None:
        """
        Handle connection completion in the main Qt thread.

        Args:
            connected: Whether connection succeeded.
        """
        self._connected = connected
        self.connection_changed.emit(connected)

        if not connected:
            return

        # Read initial value and metadata
        self._read_metadata()
        self._read_initial_value()

        # Subscribe to value changes
        self._subscription = self._caproto_pv.subscribe(data_type="time")
        self._subscription.add_callback(self._on_value_change)

    def disconnect_pv(self) -> None:
        """
        Disconnect from the PV and stop monitoring.
        """
        if self._subscription is not None:
            self._subscription.clear()
            self._subscription = None

        self._caproto_pv = None
        self._connected = False
        self.connection_changed.emit(False)

    def put(self, value: Any, wait: bool = False, timeout: float = 5.0) -> None:
        """
        Write a value to the PV.

        Args:
            value: The value to write.
            wait: If True, block until the write completes.
            timeout: Timeout in seconds if wait is True.

        Raises:
            RuntimeError: If PV is not connected.
        """
        if not self._connected or self._caproto_pv is None:
            raise RuntimeError(f"PV {self._pv_name} is not connected")

        self._caproto_pv.write(value, wait=wait, timeout=timeout)

    def _on_value_change(self, sub: Any, response: Any) -> None:
        """
        Callback for PV value changes from caproto subscription.

        This runs in a caproto background thread, so we use a queued
        signal connection to safely update the Qt main thread.

        Args:
            sub: The subscription object.
            response: The caproto subscription response.
        """
        value = response.data
        # Handle array vs scalar
        if hasattr(value, "__len__") and len(value) == 1:
            value = value[0]
        # Emit to internal signal which is queued to main thread
        self._value_received.emit(value)

    @Slot(object)
    @gui_thread_only
    def _handle_value_received(self, value: Any) -> None:
        """
        Handle value update in the main Qt thread.

        Args:
            value: The new PV value.
        """
        self._value = value
        self.value_changed.emit(value)

    def _read_metadata(self) -> None:
        """
        Read and cache PV metadata (units, limits, etc).
        """
        if self._caproto_pv is None:
            return

        try:
            # Read with control data type to get metadata
            result = self._caproto_pv.read(data_type="control")

            metadata: dict[str, Any] = {}

            # Metadata is in result.metadata for caproto
            meta = getattr(result, "metadata", None)
            if meta is None:
                meta = result

            # Extract common metadata fields
            if hasattr(meta, "units"):
                units = meta.units
                metadata["units"] = units.decode() if isinstance(units, bytes) else units

            if hasattr(meta, "lower_ctrl_limit"):
                metadata["lower_limit"] = meta.lower_ctrl_limit

            if hasattr(meta, "upper_ctrl_limit"):
                metadata["upper_limit"] = meta.upper_ctrl_limit

            if hasattr(meta, "precision"):
                metadata["precision"] = meta.precision

            if hasattr(meta, "enum_strings"):
                metadata["enum_strings"] = [
                    s.decode() if isinstance(s, bytes) else s
                    for s in meta.enum_strings
                    if s  # Filter out empty strings
                ]

            self._metadata = metadata
            self.metadata_changed.emit(metadata)

        except Exception:
            # Metadata read failed - continue without it
            pass

    def _read_initial_value(self) -> None:
        """
        Read and emit the initial PV value.
        """
        if self._caproto_pv is None:
            return

        try:
            result = self._caproto_pv.read()
            value = result.data
            # Handle array vs scalar
            if hasattr(value, "__len__") and len(value) == 1:
                value = value[0]
            self._value = value
            self.value_changed.emit(value)
        except Exception:
            # Initial read failed - subscription will provide value
            pass

    def get_introspection_data(self) -> dict[str, Any]:
        """
        Get detailed introspection data for this PV.

        This method is designed to support Claude MCP tools that inspect
        the widget tree. It provides all relevant information about the
        PV in a structured format.

        Returns:
            Dictionary containing:
                - pv_name: The EPICS PV name
                - connected: Connection status
                - value: Current value
                - metadata: All cached metadata
                - type: String representation of the value type
        """
        return {
            "pv_name": self._pv_name,
            "connected": self._connected,
            "value": self._value,
            "value_type": type(self._value).__name__ if self._value is not None else None,
            "metadata": self._metadata,
        }
