"""Spawn the Claude CLI without a console window on Windows.

The supported launch path is ``lightfall-gui.exe`` (a ``[project.gui-scripts]``
entry point), so Lightfall has no console of its own. ``claude_agent_sdk``
starts the ``claude`` CLI, which is a console program, with
``anyio.open_process(...)`` and no ``creationflags``. Windows then gives every
agent session its own visible console window. The agy backend already passes
``CREATE_NO_WINDOW`` (see ``agy/process.py``). This module does the same for
the SDK transport.

The patch is scoped: only the ``anyio`` name inside the SDK's
``subprocess_cli`` module is replaced, with a proxy whose ``open_process``
ORs in ``CREATE_NO_WINDOW``. Every other attribute goes to the real ``anyio``.
The CLI still gets a (hidden) console, so the tools it runs inherit it and
don't open windows either.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any

import anyio

CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class _NoWindowAnyio:
    """Stands in for ``anyio`` inside the SDK transport module."""

    _lightfall_no_window = True

    def __getattr__(self, name: str) -> Any:
        return getattr(anyio, name)

    @staticmethod
    async def open_process(*args: Any, **kwargs: Any) -> Any:
        kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | CREATE_NO_WINDOW
        return await anyio.open_process(*args, **kwargs)


def patch_sdk_no_console_window() -> bool:
    """Make the SDK spawn the CLI with ``CREATE_NO_WINDOW``. Idempotent.

    Returns True if the patch is (now) in place, False if it does not apply
    (not on Windows, or the SDK layout changed).
    """
    if sys.platform != "win32":
        return False
    try:
        from claude_agent_sdk._internal.transport import subprocess_cli
    except ImportError:
        return False
    current = getattr(subprocess_cli, "anyio", None)
    if getattr(current, "_lightfall_no_window", False):
        return True
    if current is not anyio:
        return False  # someone else replaced it; don't stack proxies blindly
    subprocess_cli.anyio = _NoWindowAnyio()
    return True
