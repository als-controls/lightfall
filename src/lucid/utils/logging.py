"""Logging abstraction module for LUCID.

Provides centralized logging configuration and timing utilities built on loguru.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

__all__ = [
    "logger",
    "configure_logging",
    "log_time",
    "get_cumulative_stats",
    "reset_cumulative_stats",
]

# Thread-safe cumulative timing storage
_timing_lock = threading.Lock()
_cumulative_time: dict[str, int] = defaultdict(int)
_cumulative_count: dict[str, int] = defaultdict(int)
_application_start_time = time.perf_counter_ns()

# Track whether logging has been configured
_configured = False


def _default_format(record: Record) -> str:
    """Default log format for LUCID."""
    level_colors = {
        "TRACE": "dim",
        "DEBUG": "cyan",
        "INFO": "green",
        "SUCCESS": "bold green",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "bold red",
    }
    color = level_colors.get(record["level"].name, "")
    return (
        "<dim>{time:YYYY-MM-DD HH:mm:ss.SSS}</dim> | "
        f"<{color}>{{level: <8}}</{color}> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "{message}\n{exception}"
    )


def configure_logging(
    *,
    level: str = "INFO",
    log_file: Path | str | None = None,
    rotation: str = "10 MB",
    retention: str = "1 week",
    console: bool = True,
    colorize: bool = True,
    format_string: str | None = None,
) -> None:
    """Configure logging for the LUCID application.

    This should be called once at application startup. Subsequent calls
    will reconfigure logging.

    Args:
        level: Minimum log level to display (TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Optional path to a log file. If provided, logs will also be written to file.
        rotation: When to rotate the log file (e.g., "10 MB", "1 day", "00:00").
        retention: How long to keep old log files (e.g., "1 week", "10 days").
        console: Whether to log to the console (stderr).
        colorize: Whether to use colors in console output.
        format_string: Custom format string. If None, uses the default NCS format.
    """
    global _configured

    # Remove all existing handlers
    logger.remove()

    fmt = format_string if format_string else _default_format

    if console and sys.stderr is not None:
        logger.add(
            sys.stderr,
            level=level,
            format=fmt,
            colorize=colorize,
        )

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_path,
            level=level,
            format=fmt,
            rotation=rotation,
            retention=retention,
            compression="gz",
        )

    _configured = True
    logger.debug("Logging configured with level={}", level)


@contextmanager
def log_time(
    *args: Any,
    level: str | int = "INFO",
    cumulative_key: str = "",
    precision: int = 3,
) -> Generator[None, None, None]:
    """Context manager to log the elapsed time of a code block.

    Args:
        *args: Message components to log (joined with spaces).
        level: Log level for the timing message.
        cumulative_key: If provided, accumulates timing statistics under this key.
            Use get_cumulative_stats() to retrieve aggregated data.
        precision: Decimal places for millisecond display.

    Yields:
        None

    Example:
        with log_time("Processing data"):
            process_data()

        with log_time("Database query", cumulative_key="db_queries"):
            run_query()
    """
    start = time.perf_counter_ns()
    yield
    elapsed_ns = time.perf_counter_ns() - start
    elapsed_ms = elapsed_ns / 1e6

    message_parts = [str(arg) for arg in args]

    if cumulative_key:
        with _timing_lock:
            _cumulative_time[cumulative_key] += elapsed_ns
            _cumulative_count[cumulative_key] += 1
            total_ms = _cumulative_time[cumulative_key] / 1e6
            count = _cumulative_count[cumulative_key]
            avg_ms = total_ms / count
            app_elapsed = time.perf_counter_ns() - _application_start_time
            profile_pct = (_cumulative_time[cumulative_key] / app_elapsed) * 100

        message_parts.append(
            f"[elapsed: {elapsed_ms:.{precision}f} ms | "
            f"cumulative: {total_ms:.{precision}f} ms | "
            f"avg: {avg_ms:.{precision}f} ms | "
            f"calls: {count} | "
            f"profile: {profile_pct:.1f}%]"
        )
    else:
        message_parts.append(f"[elapsed: {elapsed_ms:.{precision}f} ms]")

    logger.log(level, " ".join(message_parts))


def get_cumulative_stats(key: str | None = None) -> dict[str, dict[str, float]]:
    """Get cumulative timing statistics.

    Args:
        key: Specific key to retrieve. If None, returns all statistics.

    Returns:
        Dictionary mapping keys to their statistics:
        - total_ms: Total accumulated time in milliseconds
        - count: Number of calls
        - avg_ms: Average time per call in milliseconds
        - profile_pct: Percentage of application runtime
    """
    with _timing_lock:
        app_elapsed = time.perf_counter_ns() - _application_start_time

        def make_stats(k: str) -> dict[str, float]:
            total_ns = _cumulative_time[k]
            count = _cumulative_count[k]
            return {
                "total_ms": total_ns / 1e6,
                "count": float(count),
                "avg_ms": (total_ns / 1e6 / count) if count > 0 else 0.0,
                "profile_pct": (total_ns / app_elapsed * 100) if app_elapsed > 0 else 0.0,
            }

        if key is not None:
            return {key: make_stats(key)}
        return {k: make_stats(k) for k in _cumulative_time}


def reset_cumulative_stats(key: str | None = None) -> None:
    """Reset cumulative timing statistics.

    Args:
        key: Specific key to reset. If None, resets all statistics.
    """
    with _timing_lock:
        if key is not None:
            _cumulative_time.pop(key, None)
            _cumulative_count.pop(key, None)
        else:
            _cumulative_time.clear()
            _cumulative_count.clear()
