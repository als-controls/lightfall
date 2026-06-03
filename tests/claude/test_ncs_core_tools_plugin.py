"""Tests for NCSCoreToolPlugin as an AgentPlugin."""
from __future__ import annotations

import pytest

from lightfall.claude.ncs_core_tools import NCSCoreToolPlugin


def test_argless_construction():
    """NCSCoreToolPlugin must construct without arguments (manifest-driven)."""
    plugin = NCSCoreToolPlugin()
    assert plugin.name == "ncs_core_tools"


def test_lazy_window_lookup_returns_none_outside_qt_app():
    """Without an active QApplication, _window resolves to None and tools degrade gracefully."""
    plugin = NCSCoreToolPlugin()
    # No QApplication.instance() in this test → _window should be None
    assert plugin._window is None


def test_create_tools_returns_expected_count():
    """Tools list should match the 8 @tool-decorated functions in ncs_core_tools.py."""
    plugin = NCSCoreToolPlugin()
    tools = plugin.create_tools()
    assert len(tools) == 8
