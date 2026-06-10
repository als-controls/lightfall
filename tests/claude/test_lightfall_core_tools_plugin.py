"""Tests for LFCoreToolPlugin as an AgentPlugin."""
from __future__ import annotations

import pytest

from lightfall.claude.lightfall_core_tools import LFCoreToolPlugin


def test_argless_construction():
    """LFCoreToolPlugin must construct without arguments (manifest-driven)."""
    plugin = LFCoreToolPlugin()
    assert plugin.name == "lightfall_core_tools"


def test_lazy_window_lookup_returns_none_outside_qt_app(monkeypatch):
    """Without an active QApplication, _window resolves to None and tools degrade gracefully."""
    import PySide6.QtWidgets as _qtwidgets

    class _NoApp:
        # In a full-suite session a QApplication (and possibly a leaked
        # LFMainWindow) outlives earlier tests, so the "no Qt app" premise
        # must be forced rather than assumed.
        @staticmethod
        def instance():
            return None

    monkeypatch.setattr(_qtwidgets, "QApplication", _NoApp)
    plugin = LFCoreToolPlugin()
    assert plugin._window is None


def test_create_tools_returns_expected_count():
    """Tools list should match the 8 @tool-decorated functions in lightfall_core_tools.py."""
    plugin = LFCoreToolPlugin()
    tools = plugin.create_tools()
    assert len(tools) == 8
