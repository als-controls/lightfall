"""MCP tools for Qt widget interaction."""

from .controller import create_controller_tool
from .diagnostics import create_diagnostics_tools
from .logs import create_logs_tool
from .screenshot import create_screenshot_tool


def create_qt_tools_server(target_window):
    """
    Create the ``qt`` ToolServer with all Qt tools bound to ``target_window``.

    Args:
        target_window: The QWidget to interact with

    Returns:
        ToolServer with Qt tools
    """
    from lightfall.agents.tooling import ToolServer, as_tool_spec
    from lightfall.ui.surface.tools import create_surface_tools

    tools = [
        create_screenshot_tool(target_window),
        *create_surface_tools(target_window),
        create_controller_tool(target_window),
        create_logs_tool(),
        *create_diagnostics_tools(target_window),
    ]
    return ToolServer(
        name="qt-tools",
        version="0.1.0",
        tools=tuple(as_tool_spec(t) for t in tools),
    )


__all__ = ["create_qt_tools_server"]
