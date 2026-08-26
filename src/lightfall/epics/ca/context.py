"""Compatibility shim: SharedContext now lives in lightfall-utils."""

from lightfall_utils.ca.context import SharedContext  # noqa: F401

__all__ = ["SharedContext"]
