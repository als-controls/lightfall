"""ALS beam status service for NCS.

Polls the ALS beam status API to provide real-time synchrotron
beam information including current, energy, lifetime, and availability.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QTimer, Signal

from lucid.utils.logging import logger

# API endpoint for ALS beam status
ALS_BEAM_STATUS_URL = "https://controls.als.lbl.gov/als-beamstatus/curvals"

# Polling interval in milliseconds (60 seconds)
POLL_INTERVAL_MS = 60000


@dataclass
class ALSBeamData:
    """Structured ALS beam status data.

    Attributes:
        beam_current: Ring current in mA.
        beam_available: True if light is available (shutters open).
        beam_energy: Beam energy in GeV.
        lifetime: Beam lifetime in hours.
        x_rms: Horizontal beam position stability (microns).
        y_rms: Vertical beam position stability (microns).
        comment: Operations status message.
        timestamp: Time of the measurement.
    """

    beam_current: float = 0.0
    beam_available: bool = False
    beam_energy: float = 0.0
    lifetime: float = 0.0
    x_rms: float = 0.0
    y_rms: float = 0.0
    comment: str = ""
    timestamp: datetime | None = None


class ALSBeamStatusService(QObject):
    """Service for polling ALS beam status.

    Provides real-time ALS synchrotron beam information by polling
    the ALS beam status API. Supports automatic SOCKS5 proxy detection
    for *.lbl.gov URLs.

    Signals:
        status_changed: Emitted when beam data is updated. Carries ALSBeamData.
        connection_changed: Emitted when connection state changes. Carries bool.

    Example:
        >>> service = ALSBeamStatusService.get_instance()
        >>> service.start_polling()
        >>> print(service.current_data)
    """

    status_changed = Signal(object)  # ALSBeamData
    connection_changed = Signal(bool)  # is_connected

    _instance: ALSBeamStatusService | None = None
    _lock = threading.RLock()

    def __init__(self) -> None:
        """Initialize the ALS beam status service."""
        super().__init__()
        self._data: ALSBeamData | None = None
        self._is_connected = False
        self._last_error: str | None = None
        self._polling = False

        # Polling timer
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)

        # Proxy URL (auto-detected)
        self._proxy_url = self._get_proxy_url()

        logger.debug("ALSBeamStatusService initialized")

    @classmethod
    def get_instance(cls) -> ALSBeamStatusService:
        """Get the singleton ALSBeamStatusService instance."""
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

    @property
    def current_data(self) -> ALSBeamData | None:
        """Current beam status data, or None if not yet fetched."""
        return self._data

    @property
    def is_connected(self) -> bool:
        """Whether the service has successfully connected to the API."""
        return self._is_connected

    @property
    def last_error(self) -> str | None:
        """Last error message if connection failed."""
        return self._last_error

    @property
    def is_polling(self) -> bool:
        """Whether the service is currently polling."""
        return self._polling

    def _get_proxy_url(self) -> str | None:
        """Get the proxy URL, auto-detecting for *.lbl.gov if not specified.

        Returns:
            The proxy URL to use, or None if no proxy needed.
        """
        parsed = urlparse(ALS_BEAM_STATUS_URL)
        if parsed.hostname and parsed.hostname.endswith(".lbl.gov"):
            return "socks5://localhost:1080"
        return None

    def start_polling(self) -> None:
        """Start polling the ALS beam status API."""
        if self._polling:
            return

        logger.info("Starting ALS beam status polling")
        self._polling = True

        # Do an immediate poll, then start the timer
        self._poll()
        self._poll_timer.start(POLL_INTERVAL_MS)

    def stop_polling(self) -> None:
        """Stop polling the ALS beam status API."""
        if not self._polling:
            return

        logger.info("Stopping ALS beam status polling")
        self._polling = False
        self._poll_timer.stop()

    def _poll(self) -> None:
        """Poll the ALS beam status API."""
        try:
            data = self._fetch_beam_status()
            if data is not None:
                self._data = data
                self._set_connected(True)
                self.status_changed.emit(data)
        except Exception as e:
            logger.debug("Failed to fetch ALS beam status: {}", e)
            self._last_error = str(e)
            self._set_connected(False)

    def _set_connected(self, connected: bool) -> None:
        """Update connection state and emit signal if changed."""
        if self._is_connected != connected:
            self._is_connected = connected
            if connected:
                self._last_error = None
            self.connection_changed.emit(connected)

    def _fetch_beam_status(self) -> ALSBeamData | None:
        """Fetch beam status from the ALS API.

        Returns:
            ALSBeamData with current values, or None on error.
        """
        import httpx

        # Build client with optional SOCKS proxy
        transport = None
        if self._proxy_url:
            try:
                from httpx_socks import SyncProxyTransport

                transport = SyncProxyTransport.from_url(self._proxy_url)
            except ImportError:
                logger.debug("httpx-socks not available, trying without proxy")

        try:
            with httpx.Client(transport=transport, timeout=10.0) as client:
                response = client.get(ALS_BEAM_STATUS_URL)
                response.raise_for_status()
                return self._parse_response(response.json())
        except httpx.ConnectError:
            # Try without proxy if SOCKS connection failed
            if transport is not None:
                logger.debug("SOCKS proxy connection failed, trying direct")
                with httpx.Client(timeout=10.0) as client:
                    response = client.get(ALS_BEAM_STATUS_URL)
                    response.raise_for_status()
                    return self._parse_response(response.json())
            raise

    def _parse_response(self, data: list[dict[str, Any]]) -> ALSBeamData:
        """Parse the ALS API response into ALSBeamData.

        Args:
            data: JSON array from the API.

        Returns:
            Parsed ALSBeamData object.
        """
        # Build a lookup dict by label
        values: dict[str, str] = {}
        timestamp_str: str | None = None

        for item in data:
            label = item.get("label", "")
            val = item.get("val", "")
            values[label] = val

            # Get timestamp from Beam Current entry
            if label == "Beam Current" and "tstamp" in item:
                timestamp_str = item["tstamp"]

        # Parse timestamp
        timestamp = None
        if timestamp_str:
            try:
                timestamp = datetime.fromtimestamp(int(timestamp_str))
            except (ValueError, OSError):
                pass

        return ALSBeamData(
            beam_current=self._safe_float(values.get("Beam Current", "0")),
            beam_available=values.get("Beam Available", "0") == "1",
            beam_energy=self._safe_float(values.get("Beam Energy", "0")),
            lifetime=self._safe_float(values.get("Lifetime", "0")),
            x_rms=self._safe_float(values.get("X RMS Avg", "0")),
            y_rms=self._safe_float(values.get("Y RMS Avg", "0")),
            comment=values.get("Comment", ""),
            timestamp=timestamp,
        )

    def _safe_float(self, value: str) -> float:
        """Safely parse a float from a string.

        Args:
            value: String to parse.

        Returns:
            Parsed float, or 0.0 on error.
        """
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    def force_refresh(self) -> None:
        """Force an immediate refresh of beam status."""
        self._poll()

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with current beam status information.
        """
        result: dict[str, Any] = {
            "is_connected": self._is_connected,
            "is_polling": self._polling,
        }

        if self._data:
            result["beam_current_mA"] = self._data.beam_current
            result["beam_available"] = self._data.beam_available
            result["beam_energy_GeV"] = self._data.beam_energy
            result["lifetime_hours"] = self._data.lifetime
            result["x_rms_microns"] = self._data.x_rms
            result["y_rms_microns"] = self._data.y_rms
            result["comment"] = self._data.comment
            if self._data.timestamp:
                result["timestamp"] = self._data.timestamp.isoformat()

        if self._last_error:
            result["last_error"] = self._last_error

        return result
