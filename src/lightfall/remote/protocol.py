"""Reply protocol for the remote-control contract (v1).

Every reply — success or error — carries ``contract_version`` so clients can
detect mismatches. Errors are structured: ``{status: "error", code, message}``.
"""

from __future__ import annotations

from typing import Any

__all__ = ["CONTRACT_VERSION", "ERROR_CODES", "error_reply", "ok_reply"]

CONTRACT_VERSION = 1

ERROR_CODES = frozenset(
    {"busy", "limits", "timeout", "unknown", "denied", "bad_request", "version_mismatch"}
)


def ok_reply(**fields: Any) -> dict:
    """Build a success reply carrying ``contract_version``."""
    return {**fields, "contract_version": CONTRACT_VERSION}


def error_reply(code: str, message: str) -> dict:
    """Build a structured error reply.

    Args:
        code: One of :data:`ERROR_CODES`.
        message: Human-readable detail.

    Raises:
        ValueError: If *code* is not a known error code.
    """
    if code not in ERROR_CODES:
        raise ValueError(f"Unknown error code: {code!r}")
    return {
        "status": "error",
        "code": code,
        "message": message,
        "contract_version": CONTRACT_VERSION,
    }
