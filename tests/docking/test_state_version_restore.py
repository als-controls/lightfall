"""DockingState.restore() must survive the QSettings string/int round-trip.

STATE_VERSION is an int, but QSettings (IniFormat, and the Windows registry
backend in practice) returns values read back without a type hint as strings.
So a saved version of int 6 comes back as the string "6", and the bare
`version != STATE_VERSION` check (`"6" != 6`) is always true -- meaning a
perfectly valid saved dock layout is treated as a version mismatch and silently
discarded on every launch. restore() must coerce the version before comparing.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDockWidget, QMainWindow

from lightfall.ui.docking.state import STATE_VERSION, DockingState


def _window_with_dock(qtbot) -> QMainWindow:
    window = QMainWindow()
    qtbot.addWidget(window)
    dock = QDockWidget("d", window)
    dock.setObjectName("dock_d")  # named dock -> saveState/restoreState round-trips
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
    return window


def test_restore_succeeds_after_ini_roundtrip(tmp_path, qtbot):
    """save() then restore() through a *fresh* IniFormat QSettings (version
    comes back as the string "6") must restore, not bail on a false mismatch."""
    window = _window_with_dock(qtbot)
    state = DockingState(window)
    ini = str(tmp_path / "docking.ini")

    save_settings = QSettings(ini, QSettings.Format.IniFormat)
    state.save(save_settings)
    save_settings.sync()

    # New session reading the same ini: IniFormat returns "version" as str "6".
    restore_settings = QSettings(ini, QSettings.Format.IniFormat)
    assert state.restore(restore_settings) is True


def test_restore_still_bails_on_genuine_version_mismatch(tmp_path, qtbot):
    """A genuinely older saved version must still be rejected (migration
    handling preserved) -- coercion must compare values, not just types."""
    window = _window_with_dock(qtbot)
    state = DockingState(window)
    ini = str(tmp_path / "docking.ini")

    s = QSettings(ini, QSettings.Format.IniFormat)
    state.save(s)
    # Overwrite the stored version with an older one.
    s.beginGroup("docking")
    s.setValue("version", STATE_VERSION - 1)
    s.endGroup()
    s.sync()

    s2 = QSettings(ini, QSettings.Format.IniFormat)
    assert state.restore(s2) is False
