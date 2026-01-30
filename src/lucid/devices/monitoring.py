"""Device monitoring and metrics collection.

This module provides tools for monitoring device health and
collecting metrics for performance analysis and predictive
maintenance.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from loguru import logger
from PySide6.QtCore import QObject, QTimer, Signal

if TYPE_CHECKING:
    from lucid.devices.catalog import DeviceCatalog


@dataclass
class DeviceMetric:
    """A single metric measurement for a device."""

    device_id: UUID
    metric_name: str
    value: float
    timestamp: datetime = field(default_factory=datetime.now)
    unit: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceHealth:
    """Health summary for a device."""

    device_id: UUID
    device_name: str
    is_healthy: bool
    status: str  # "good", "warning", "error", "unknown"
    last_seen: datetime | None
    error_count: int = 0
    warning_count: int = 0
    uptime_percent: float = 100.0
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "device_id": str(self.device_id),
            "device_name": self.device_name,
            "is_healthy": self.is_healthy,
            "status": self.status,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "uptime_percent": self.uptime_percent,
            "issues": self.issues,
        }


class DeviceMetricsCollector(QObject):
    """Collects and stores device metrics over time.

    DeviceMetricsCollector periodically polls devices to collect
    metrics like position, value, connection status, and stores
    them for trend analysis.

    Signals:
        metric_collected: Emitted when a metric is collected.
        health_changed: Emitted when device health status changes.

    Example:
        >>> collector = DeviceMetricsCollector(catalog)
        >>> collector.start(interval_ms=1000)
        >>> collector.metric_collected.connect(on_metric)
    """

    _instance: ClassVar[DeviceMetricsCollector | None] = None

    metric_collected = Signal(object)  # DeviceMetric
    health_changed = Signal(str, object)  # device_id, DeviceHealth

    def __init__(
        self,
        catalog: DeviceCatalog,
        max_history: int = 1000,
        parent: QObject | None = None,
    ) -> None:
        """Initialize the metrics collector.

        Args:
            catalog: Device catalog to monitor.
            max_history: Maximum number of metrics to retain per device.
            parent: Optional Qt parent.
        """
        super().__init__(parent)
        self._catalog = catalog
        self._max_history = max_history

        # Metric storage: device_id -> metric_name -> deque of metrics
        self._metrics: dict[UUID, dict[str, deque[DeviceMetric]]] = {}

        # Health tracking
        self._health: dict[UUID, DeviceHealth] = {}
        self._last_seen: dict[UUID, datetime] = {}
        self._error_counts: dict[UUID, int] = {}

        # Polling timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._collect_metrics)
        self._running = False

    @classmethod
    def get_instance(
        cls, catalog: DeviceCatalog | None = None
    ) -> DeviceMetricsCollector | None:
        """Get or create the singleton instance.

        Args:
            catalog: Device catalog (required for first call).

        Returns:
            The collector instance or None if not initialized.
        """
        if cls._instance is None and catalog is not None:
            cls._instance = cls(catalog)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance."""
        if cls._instance is not None:
            cls._instance.stop()
            cls._instance = None

    @property
    def is_running(self) -> bool:
        """Check if collection is running."""
        return self._running

    def start(self, interval_ms: int = 5000) -> None:
        """Start collecting metrics.

        Args:
            interval_ms: Collection interval in milliseconds.
        """
        if self._running:
            return

        self._timer.start(interval_ms)
        self._running = True
        logger.info("Device metrics collection started (interval: {}ms)", interval_ms)

    def stop(self) -> None:
        """Stop collecting metrics."""
        if not self._running:
            return

        self._timer.stop()
        self._running = False
        logger.info("Device metrics collection stopped")

    def _collect_metrics(self) -> None:
        """Collect metrics from all devices."""
        devices = self._catalog.get_all_devices()

        for device in devices:
            try:
                self._collect_device_metrics(device.id)
            except Exception as e:
                logger.error("Error collecting metrics for {}: {}", device.name, e)
                self._record_error(device.id, device.name, str(e))

    def _collect_device_metrics(self, device_id: UUID) -> None:
        """Collect metrics for a single device."""
        device = self._catalog.get_device(device_id)
        if device is None:
            return

        now = datetime.now()
        self._last_seen[device_id] = now

        # Initialize storage if needed
        if device_id not in self._metrics:
            self._metrics[device_id] = {}

        # Refresh device state
        state = self._catalog.refresh_device_state(device_id)

        if state is not None:
            # Collect position metric
            if state.position is not None:
                self._store_metric(DeviceMetric(
                    device_id=device_id,
                    metric_name="position",
                    value=float(state.position),
                    unit=device.metadata.get("units", ""),
                ))

            # Collect value metric
            if state.value is not None:
                try:
                    value = float(state.value)
                    self._store_metric(DeviceMetric(
                        device_id=device_id,
                        metric_name="value",
                        value=value,
                        unit=device.metadata.get("units", ""),
                    ))
                except (TypeError, ValueError):
                    pass

            # Collect connection status
            self._store_metric(DeviceMetric(
                device_id=device_id,
                metric_name="connected",
                value=1.0 if state.connected else 0.0,
            ))

            # Update health
            self._update_health(device_id, device.name, state.connected)
        else:
            # Device state unavailable
            self._store_metric(DeviceMetric(
                device_id=device_id,
                metric_name="connected",
                value=0.0,
            ))
            self._update_health(device_id, device.name, False)

    def _store_metric(self, metric: DeviceMetric) -> None:
        """Store a metric in the history."""
        device_metrics = self._metrics.setdefault(metric.device_id, {})
        metric_history = device_metrics.setdefault(
            metric.metric_name,
            deque(maxlen=self._max_history),
        )
        metric_history.append(metric)
        self.metric_collected.emit(metric)

    def _record_error(self, device_id: UUID, device_name: str, error: str) -> None:
        """Record an error for a device."""
        self._error_counts[device_id] = self._error_counts.get(device_id, 0) + 1
        self._update_health(device_id, device_name, False, error)

    def _update_health(
        self,
        device_id: UUID,
        device_name: str,
        connected: bool,
        error: str | None = None,
    ) -> None:
        """Update health status for a device."""
        old_health = self._health.get(device_id)

        # Calculate health status
        error_count = self._error_counts.get(device_id, 0)
        issues = []

        if not connected:
            issues.append("Device not connected")

        if error:
            issues.append(error)

        if error_count > 10:
            status = "error"
            is_healthy = False
        elif error_count > 3 or not connected:
            status = "warning"
            is_healthy = False
        else:
            status = "good"
            is_healthy = True

        # Calculate uptime (simplified)
        uptime = 100.0 if connected else 0.0

        health = DeviceHealth(
            device_id=device_id,
            device_name=device_name,
            is_healthy=is_healthy,
            status=status,
            last_seen=self._last_seen.get(device_id),
            error_count=error_count,
            uptime_percent=uptime,
            issues=issues,
        )

        self._health[device_id] = health

        # Emit signal if status changed
        if old_health is None or old_health.status != health.status:
            self.health_changed.emit(str(device_id), health)

    # === Query Methods ===

    def get_metrics(
        self,
        device_id: UUID | str,
        metric_name: str,
        since: datetime | None = None,
    ) -> list[DeviceMetric]:
        """Get metrics for a device.

        Args:
            device_id: Device ID.
            metric_name: Name of metric to retrieve.
            since: Only return metrics after this time.

        Returns:
            List of metrics.
        """
        if isinstance(device_id, str):
            device_id = UUID(device_id)

        device_metrics = self._metrics.get(device_id, {})
        metric_history = device_metrics.get(metric_name, deque())

        if since is None:
            return list(metric_history)

        return [m for m in metric_history if m.timestamp >= since]

    def get_latest_metric(
        self,
        device_id: UUID | str,
        metric_name: str,
    ) -> DeviceMetric | None:
        """Get the most recent metric for a device.

        Args:
            device_id: Device ID.
            metric_name: Name of metric.

        Returns:
            Latest metric or None.
        """
        if isinstance(device_id, str):
            device_id = UUID(device_id)

        device_metrics = self._metrics.get(device_id, {})
        metric_history = device_metrics.get(metric_name)

        if metric_history:
            return metric_history[-1]
        return None

    def get_device_health(self, device_id: UUID | str) -> DeviceHealth | None:
        """Get health status for a device.

        Args:
            device_id: Device ID.

        Returns:
            Device health or None.
        """
        if isinstance(device_id, str):
            device_id = UUID(device_id)
        return self._health.get(device_id)

    def get_all_health(self) -> list[DeviceHealth]:
        """Get health status for all devices.

        Returns:
            List of device health objects.
        """
        return list(self._health.values())

    def get_unhealthy_devices(self) -> list[DeviceHealth]:
        """Get devices that are not healthy.

        Returns:
            List of unhealthy device health objects.
        """
        return [h for h in self._health.values() if not h.is_healthy]

    def get_metric_statistics(
        self,
        device_id: UUID | str,
        metric_name: str,
        window: timedelta | None = None,
    ) -> dict[str, float] | None:
        """Get statistics for a metric.

        Args:
            device_id: Device ID.
            metric_name: Metric name.
            window: Time window for statistics (None = all data).

        Returns:
            Dictionary with min, max, mean, std, count.
        """
        if window:
            since = datetime.now() - window
        else:
            since = None

        metrics = self.get_metrics(device_id, metric_name, since)
        if not metrics:
            return None

        values = [m.value for m in metrics]
        n = len(values)

        if n == 0:
            return None

        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n if n > 1 else 0
        std = variance ** 0.5

        return {
            "min": min(values),
            "max": max(values),
            "mean": mean,
            "std": std,
            "count": n,
        }

    # === Introspection ===

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for Claude MCP tools.

        Returns:
            Dictionary with collector information.
        """
        return {
            "running": self._running,
            "monitored_devices": len(self._metrics),
            "health_summary": {
                "healthy": len([h for h in self._health.values() if h.is_healthy]),
                "unhealthy": len([h for h in self._health.values() if not h.is_healthy]),
            },
            "unhealthy_devices": [
                h.to_dict() for h in self.get_unhealthy_devices()
            ],
        }
