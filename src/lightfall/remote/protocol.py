"""Lightfall's remote protocol module — the implementation lives in ``lightfall_utils.ipc``."""

from lightfall_utils.ipc.protocol import BASE_ERROR_CODES as ERROR_CODES  # noqa: F401
from lightfall_utils.ipc.protocol import CONTRACT_VERSION, error_reply, ok_reply  # noqa: F401

__all__ = ["CONTRACT_VERSION", "ERROR_CODES", "error_reply", "ok_reply"]
