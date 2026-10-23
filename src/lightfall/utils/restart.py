"""Restart Lightfall: quit through the normal close path, then start again.

:func:`request_restart` asks the app to quit and remembers why; ``main()``
calls :func:`relaunch` once the event loop has returned and cleanup has run,
so the new process never overlaps the old one's live state.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from lightfall.utils.logging import logger

_RESTART_REQUESTED = False


def request_restart() -> None:
    """Quit Lightfall normally and start it again afterwards."""
    global _RESTART_REQUESTED
    from PySide6.QtCore import QCoreApplication, QTimer

    _RESTART_REQUESTED = True
    logger.info("Restart requested")
    QTimer.singleShot(0, QCoreApplication.quit)


def restart_requested() -> bool:
    return _RESTART_REQUESTED


def relaunch(
    argv: list[str] | None = None, popen: Callable[..., Any] = subprocess.Popen
) -> bool:
    """Start a detached copy of this process (``sys.orig_argv`` by default)."""
    args = list(argv or sys.orig_argv)
    kwargs: dict[str, Any] = {"close_fds": True, "cwd": os.getcwd()}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        popen(args, **kwargs)
    except OSError as exc:
        logger.error("Could not restart Lightfall ({}): {}", args, exc)
        return False
    logger.info("Relaunched Lightfall: {}", args)
    return True
