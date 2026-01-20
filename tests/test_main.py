"""Tests for the main module."""

from ncs.main import NCSMainWindow


def test_main_window_creation(qapp) -> None:
    """Test that the main window can be created."""
    window = NCSMainWindow()
    assert window.windowTitle() == "NCS - New Control System"
    assert window.minimumWidth() == 1024
    assert window.minimumHeight() == 768
