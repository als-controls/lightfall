"""Antigravity CLI (``agy``) agent backend.

Exports are resolved lazily: agy runs ``python -I -m
lightfall.agents.backends.agy.hook_client`` for every tool call, which imports
this package first, so it must stay import-cheap (no uvicorn/mcp here).
"""
from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "AgyCliBackend": "backend",
    "AgyEventTranslator": "events",
    "AgyProcess": "process",
    "agy_signed_in": "discover",
    "console_python": "discover",
    "find_agy": "discover",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{mod}"), name)
