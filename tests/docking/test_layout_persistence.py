"""Tests for window-layout persistence across restarts.

setup_default_layout() must wipe saved state and suppress showEvent()
restoration ONLY on first run; when a saved layout exists it must be left
intact so showEvent() can restore it. Following test_proactive_latch.py,
the mainwindow logic is exercised on a minimal stand-in rather than a full
LFMainWindow (which drags in the whole app stack).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMainWindow

from lightfall.ui.docking.state import DockingState
from lightfall.ui.mainwindow import LFMainWindow


class _LayoutHost:
    """Minimal stand-in providing what setup_default_layout() touches."""

    def __init__(self):
        self._docking_manager = MagicMock()
        self._session_manager = MagicMock()
        self._panel_registry = MagicMock()
        self._panel_registry.list_by_area.return_value = []
        self._default_layout_applied = False
        self.add_panel = MagicMock()
        self.register_deferred_panel = MagicMock(return_value=True)
        self._apply_theme = MagicMock()


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    """Redirect QSettings("ALS", "NCS") construction inside mainwindow.py to
    a throwaway ini file, so tests never read or wipe the real registry.

    Returns a factory for inspecting/seeding the same ini file.
    """
    import PySide6.QtCore as QtCore

    real_qsettings = QtCore.QSettings
    ini = str(tmp_path / "settings.ini")

    def make(*args, **kwargs):
        return real_qsettings(ini, real_qsettings.Format.IniFormat)

    monkeypatch.setattr(QtCore, "QSettings", make)
    return make


def test_first_run_applies_default_and_suppresses_restore(temp_settings):
    """No saved layout: stale state is cleared and showEvent() restore is
    suppressed via the _default_layout_applied latch."""
    host = _LayoutHost()
    host._docking_manager.has_saved_state.return_value = False

    LFMainWindow.setup_default_layout(host)

    assert host._default_layout_applied is True
    host._docking_manager.clear_state.assert_called_once()


def test_saved_geometry_is_preserved_for_restore(temp_settings):
    """A saved geometry must survive setup_default_layout() and leave the
    restore path enabled."""
    settings = temp_settings()
    settings.setValue("mainwindow/geometry", b"saved-geometry")
    settings.sync()

    host = _LayoutHost()
    host._docking_manager.has_saved_state.return_value = False

    LFMainWindow.setup_default_layout(host)

    assert host._default_layout_applied is False
    host._docking_manager.clear_state.assert_not_called()
    assert temp_settings().value("mainwindow/geometry") is not None


def test_saved_docking_state_alone_counts_as_saved_layout(temp_settings):
    """Docking state without geometry still means "user has a layout"."""
    host = _LayoutHost()
    host._docking_manager.has_saved_state.return_value = True

    LFMainWindow.setup_default_layout(host)

    assert host._default_layout_applied is False
    host._docking_manager.clear_state.assert_not_called()


def test_docking_state_has_saved_state_roundtrip(tmp_path, qtbot):
    """DockingState.has_saved_state tracks save() and clear()."""
    window = QMainWindow()
    qtbot.addWidget(window)
    state = DockingState(window)
    settings = QSettings(
        str(tmp_path / "docking.ini"), QSettings.Format.IniFormat
    )

    assert state.has_saved_state(settings) is False
    assert state.has_saved_state(None) is False

    state.save(settings)
    assert state.has_saved_state(settings) is True

    state.clear(settings)
    assert state.has_saved_state(settings) is False
