"""Shared fixtures for theater mode tests."""

import pytest
from PySide6.QtWidgets import QVBoxLayout, QWidget


@pytest.fixture()
def parent_widget(qtbot):
    """A parent widget with a layout, simulating a panel interior."""
    w = QWidget()
    w.setObjectName("TestParent")
    w.resize(800, 600)
    QVBoxLayout(w)
    qtbot.addWidget(w)
    w.show()
    return w


@pytest.fixture(autouse=True)
def _reset_theater_manager():
    """Reset the theater manager singleton between tests."""
    from lightfall.ui.theater.manager import theater_manager

    theater_manager._proxies.clear()
    theater_manager._overlay = None
    yield
    theater_manager._proxies.clear()
    theater_manager._overlay = None
