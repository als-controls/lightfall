"""Compatibility shim: caproto shutdown helpers now live in lightfall-utils."""

from lightfall_utils.caproto_shutdown import (  # noqa: F401
    disconnect_context,
    drain_callback_executors,
    get_caproto_context,
)

__all__ = ["get_caproto_context", "drain_callback_executors", "disconnect_context"]
