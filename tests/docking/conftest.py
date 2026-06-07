"""Shared fixtures for docking system tests."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMainWindow

from lightfall.ui.docking.manager import DockingManager
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.panels.registry import PanelRegistry


@pytest.fixture(autouse=True)
def _reset_panel_registry():
    """Fresh PanelRegistry singleton per test."""
    PanelRegistry.reset()
    yield
    PanelRegistry.reset()


@pytest.fixture(autouse=True)
def _reset_theater_manager():
    """Reset the theater manager singleton between tests.

    PanelDockWidget wraps panels in TheaterProxy, which registers
    globally — without this, proxies leak across tests.
    """
    from lightfall.ui.theater.manager import theater_manager

    theater_manager._proxies.clear()
    theater_manager._overlay = None
    yield
    theater_manager._proxies.clear()
    theater_manager._overlay = None


@pytest.fixture(autouse=True)
def _temp_qsettings(tmp_path, monkeypatch):
    """Route the docking manager's QSettings to a throwaway ini file.

    Prevents tests from reading/writing the real ALS/NCS settings.
    """

    def make_settings(*args, **kwargs):
        return QSettings(
            str(tmp_path / "test_settings.ini"), QSettings.Format.IniFormat
        )

    monkeypatch.setattr(
        "lightfall.ui.docking.manager.QSettings", make_settings
    )


def make_panel_class(
    panel_id: str,
    *,
    name: str | None = None,
    icon: str = "mdi6.alpha-a",
    area: str = "left",
    order: int = 0,
    proactive: bool = True,
):
    """Build a throwaway BasePanel subclass with given metadata."""
    meta = PanelMetadata(
        id=panel_id,
        name=name or panel_id,
        icon=icon,
        default_area=area,
        sidebar_order=order,
        proactive_init=proactive,
    )
    return type(
        "TestPanel_" + panel_id.replace(".", "_"),
        (BasePanel,),
        {"panel_metadata": meta},
    )


@pytest.fixture()
def docking(qtbot):
    """An initialized DockingManager hosted in a shown QMainWindow."""
    window = QMainWindow()
    window.resize(1200, 800)
    qtbot.addWidget(window)
    manager = DockingManager(window)
    manager.initialize()
    window.show()
    return manager
