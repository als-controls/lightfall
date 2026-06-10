"""Pytest configuration and fixtures for Lightfall tests."""

# pytest-qt provides the `qapp` and `qtbot` fixtures automatically.
# No custom qapp fixture needed - pytest-qt handles QApplication lifecycle
# including proper cleanup and CI/headless environment support.
