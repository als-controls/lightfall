"""suppress_windows_frame: DWM border/corner suppression for frameless windows."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from lightfall.ui.style import windows_frame


def test_noop_off_windows(monkeypatch):
    monkeypatch.setattr(windows_frame.sys, "platform", "linux")
    window = MagicMock()
    windows_frame.suppress_windows_frame(window)
    window.winId.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="DWM is Windows only")
def test_sets_border_and_corner_attributes(qtbot, monkeypatch):
    import ctypes

    from PySide6.QtWidgets import QWidget

    calls = []

    def fake_set(hwnd, attribute, data, size):
        calls.append((attribute, ctypes.cast(data, ctypes.POINTER(ctypes.c_uint)).contents.value))
        return 0

    monkeypatch.setattr(ctypes.windll.dwmapi, "DwmSetWindowAttribute", fake_set)
    widget = QWidget()
    qtbot.addWidget(widget)
    windows_frame.suppress_windows_frame(widget)
    assert calls == [(windows_frame.DWMWA_BORDER_COLOR, windows_frame.DWMWA_COLOR_NONE),
                     (windows_frame.DWMWA_WINDOW_CORNER_PREFERENCE,
                      windows_frame.DWMWCP_DONOTROUND)]


def test_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(windows_frame.sys, "platform", "win32")
    window = MagicMock()
    window.winId.side_effect = RuntimeError("no native window")
    windows_frame.suppress_windows_frame(window)  # must not raise
