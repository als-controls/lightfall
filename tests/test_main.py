"""Tests for the main module."""

from lightfall.ui import NCSMainWindow


def test_main_window_creation(qapp) -> None:
    """Test that the main window can be created."""
    window = NCSMainWindow()
    assert window.windowTitle() == "Lightfall"
    assert window.minimumWidth() == 1024
    assert window.minimumHeight() == 768
