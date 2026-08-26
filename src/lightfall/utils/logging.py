"""Compatibility shim: the logging module now lives in lightfall-utils."""

from lightfall_utils.logging import (  # noqa: F401
    configure_logging,
    get_cumulative_stats,
    log_time,
    logger,
    reset_cumulative_stats,
)

__all__ = [
    "logger",
    "configure_logging",
    "log_time",
    "get_cumulative_stats",
    "reset_cumulative_stats",
]
