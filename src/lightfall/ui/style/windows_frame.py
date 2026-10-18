"""Suppress the Windows 11 border and corner rounding on frameless top-level windows."""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

# DwmSetWindowAttribute ids and values (dwmapi.h, Windows 11 22000+).
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND = 1
DWMWA_BORDER_COLOR = 34
DWMWA_COLOR_NONE = 0xFFFFFFFE


def suppress_windows_frame(window: QWidget) -> None:
    """Windows 11 draws a 1 px border and rounds the corners of every top-level window,
    frameless ones included. On a window that paints its own rounded card inside a
    transparent margin (the setup wizard, toasts) that border outlines the margin, so
    turn both off. Call after the native window exists (e.g. from showEvent or after
    show()). A no-op elsewhere and on older Windows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = ctypes.c_void_p(int(window.winId()))
        dwm = ctypes.windll.dwmapi
        for attribute, value in ((DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE),
                                 (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND)):
            data = ctypes.c_uint(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))
    except Exception as exc:  # noqa: BLE001 - cosmetic; never break a window over it
        logger.debug("Could not remove the Windows window border: {}", exc)
