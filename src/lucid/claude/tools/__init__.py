"""MCP tools for Qt widget interaction."""

from .controller import create_controller_tool
from .interaction import create_interaction_tools
from .introspection import create_introspection_tools
from .screenshot import create_screenshot_tool


def create_qt_tools_server(target_window):
    """
    Create MCP server with all Qt tools.

    Args:
        target_window: The QWidget to interact with

    Returns:
        MCP server instance with Qt tools
    """
    from claude_agent_sdk import create_sdk_mcp_server

    # Create all tools bound to the target window
    screenshot_tool = create_screenshot_tool(target_window)
    get_tree_tool, find_widget_tool = create_introspection_tools(target_window)
    click_tool, type_tool = create_interaction_tools(target_window)
    controller_tool = create_controller_tool(target_window)

    # Create and return the server
    return create_sdk_mcp_server(
        name="qt-tools",
        version="0.1.0",
        tools=[
            screenshot_tool,
            get_tree_tool,
            find_widget_tool,
            click_tool,
            type_tool,
            controller_tool,
        ]
    )


__all__ = ["create_qt_tools_server"]
