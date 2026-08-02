"""Guard test for ClaudePanel bus-endpoint registration.

When ClaudeAssistantWidget.__init__ takes the error-UI path (e.g. no API key
configured) it returns early without creating ``bus_endpoint``. Before the
fix, ``_setup_claude_widget`` dereferenced ``self._claude_widget.bus_endpoint``
unguarded; the AttributeError was swallowed by the generic except in
``_initialize_claude_widget``, replacing the clean "API key not provided"
error with a confusing one. ``_register_bus_endpoint`` must no-op instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.ui.panels.claude_panel import ClaudePanel


@pytest.fixture
def panel(qtbot, monkeypatch):
    monkeypatch.setattr(ClaudePanel, "_setup_ui", lambda self: None)
    p = ClaudePanel()
    qtbot.addWidget(p)
    return p


def test_register_bus_endpoint_noops_without_bus_endpoint_attr(panel):
    # A widget lacking bus_endpoint (the error-UI path) -- spec=[] means
    # hasattr() genuinely returns False rather than auto-vivifying an attr.
    widget = MagicMock(spec=[])
    panel._claude_widget = widget

    panel._register_bus_endpoint()  # must not raise


def test_register_bus_endpoint_registers_when_present(panel, monkeypatch):
    widget = MagicMock(spec=["bus_endpoint", "agent"])
    widget.bus_endpoint.name = "lightfall"
    panel._claude_widget = widget

    fake_bus = MagicMock()
    fake_bus.register.return_value = "lightfall-2"
    monkeypatch.setattr(
        "lightfall.agents.bus.AgentBus.get_instance", lambda: fake_bus
    )

    panel._register_bus_endpoint()

    fake_bus.register.assert_called_once_with("lightfall", widget.bus_endpoint)
    assert widget.bus_endpoint.name == "lightfall-2"
    assert widget.agent.bus_name == "lightfall-2"
