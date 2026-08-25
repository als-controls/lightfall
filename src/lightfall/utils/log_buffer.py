"""Compatibility shim: the log ring buffer now lives in lightfall-utils."""

from lightfall_utils.log_buffer import LogBuffer, LogRecord, get_log_buffer  # noqa: F401

__all__ = ["LogBuffer", "LogRecord", "get_log_buffer"]
