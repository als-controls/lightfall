"""Error collector for capturing recent errors from loguru.

Provides a thread-safe singleton that captures ERROR+ level logs for use in
bug reporting dialogs and error inspection tools.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loguru import Record


@dataclass
class ErrorRecord:
    """A captured error record from loguru.

    Attributes:
        timestamp: When the error occurred (UTC).
        level: Log level name (ERROR, CRITICAL, etc.).
        module: Module where the error occurred.
        function: Function name where the error occurred.
        line: Line number where the error occurred.
        message: The error message.
        exception_info: Formatted exception traceback, if any.
    """

    timestamp: datetime
    level: str
    module: str
    function: str
    line: int
    message: str
    exception_info: str | None = None

    # Unique ID for this error record
    id: str = field(default_factory=lambda: "")

    def __post_init__(self) -> None:
        """Generate a unique ID if not provided."""
        if not self.id:
            # Create a short ID from timestamp and message hash
            ts_part = self.timestamp.strftime("%H%M%S")
            msg_hash = abs(hash(self.message)) % 10000
            self.id = f"{ts_part}-{msg_hash:04d}"

    @property
    def short_message(self) -> str:
        """Get a truncated message suitable for display in lists.

        Returns:
            Message truncated to 80 characters with ellipsis if needed.
        """
        if len(self.message) <= 80:
            return self.message
        return self.message[:77] + "..."

    @property
    def location(self) -> str:
        """Get the error location as 'module:function:line'.

        Returns:
            Formatted location string.
        """
        return f"{self.module}:{self.function}:{self.line}"

    @property
    def display_time(self) -> str:
        """Get the timestamp formatted for display.

        Returns:
            Local time formatted as 'HH:MM:SS'.
        """
        local_time = self.timestamp.astimezone()
        return local_time.strftime("%H:%M:%S")


class ErrorCollector:
    """Singleton that captures ERROR+ level logs from loguru.

    The collector installs a loguru sink that buffers the most recent errors
    for use in bug reporting and error inspection. Errors older than MAX_AGE
    are automatically filtered out when retrieving.

    Usage:
        # Install at application startup (after logging is configured)
        ErrorCollector.get_instance().install()

        # Get recent errors for bug report dialog
        errors = ErrorCollector.get_instance().get_recent_errors()
    """

    MAX_ERRORS = 50
    MAX_AGE = timedelta(hours=24)

    _instance: ErrorCollector | None = None
    _lock = threading.Lock()

    def __new__(cls) -> ErrorCollector:
        """Get or create the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """Initialize the error collector."""
        if self._initialized:
            return
        self._initialized = True

        # Thread-safe error buffer (ring buffer with max size)
        self._errors: deque[ErrorRecord] = deque(maxlen=self.MAX_ERRORS)
        self._buffer_lock = threading.Lock()
        self._sink_id: int | None = None

    @classmethod
    def get_instance(cls) -> ErrorCollector:
        """Get the singleton instance.

        Returns:
            The ErrorCollector singleton.
        """
        return cls()

    def install(self) -> None:
        """Install the loguru sink to capture ERROR+ level logs.

        This should be called once at application startup, after logging
        is configured. Safe to call multiple times (subsequent calls are no-ops).
        """
        if self._sink_id is not None:
            return  # Already installed

        from loguru import logger

        # Add sink for ERROR and above
        # Note: level="ERROR" already filters to ERROR (40) and above
        self._sink_id = logger.add(
            self._sink,
            level="ERROR",
            format="{message}",  # We extract details from the record directly
        )

    def uninstall(self) -> None:
        """Remove the loguru sink.

        Primarily useful for testing.
        """
        if self._sink_id is None:
            return

        from loguru import logger

        logger.remove(self._sink_id)
        self._sink_id = None

    def _sink(self, message) -> None:
        """Loguru sink handler that captures error records.

        Args:
            message: The loguru Message object (str subclass with record attribute).
        """
        # Access the record from the message object
        # message is a loguru Message (str subclass) with a .record attribute
        record: Record = message.record

        # Extract exception info if present
        exception_info = None
        if record["exception"] is not None:
            exc = record["exception"]
            if exc.traceback:
                import traceback

                exception_info = "".join(
                    traceback.format_exception(exc.type, exc.value, exc.traceback)
                )

        # Create error record
        error = ErrorRecord(
            timestamp=datetime.now(UTC),
            level=record["level"].name,
            module=record["module"],
            function=record["function"],
            line=record["line"],
            message=record["message"],
            exception_info=exception_info,
        )

        # Add to buffer (thread-safe)
        with self._buffer_lock:
            self._errors.append(error)

    def get_recent_errors(self, max_count: int | None = None) -> list[ErrorRecord]:
        """Get recent errors, filtered by age.

        Args:
            max_count: Maximum number of errors to return. If None, returns
                all errors within MAX_AGE (up to MAX_ERRORS).

        Returns:
            List of ErrorRecord objects, newest first.
        """
        cutoff = datetime.now(UTC) - self.MAX_AGE

        with self._buffer_lock:
            # Filter by age and reverse for newest-first order
            recent = [e for e in self._errors if e.timestamp > cutoff]
            recent.reverse()

        if max_count is not None:
            recent = recent[:max_count]

        return recent

    def clear(self) -> None:
        """Clear all captured errors.

        Primarily useful for testing.
        """
        with self._buffer_lock:
            self._errors.clear()

    def __len__(self) -> int:
        """Get the number of captured errors."""
        with self._buffer_lock:
            return len(self._errors)


def get_error_collector() -> ErrorCollector:
    """Get the ErrorCollector singleton.

    Convenience function for accessing the error collector.

    Returns:
        The ErrorCollector singleton instance.
    """
    return ErrorCollector.get_instance()
