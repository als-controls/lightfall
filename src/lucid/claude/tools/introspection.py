"""Widget tree introspection tools."""

import json
from typing import Any

from PySide6.QtWidgets import QWidget

from lucid.claude._internal.serialization import (
    find_widget_by_name,
    get_widget_summary,
    serialize_widget,
    serialize_widget_tree,
)
from lucid.claude._internal.threading import run_on_main_thread


def create_introspection_tools(target_window: QWidget):
    """
    Create introspection tools bound to a specific widget.

    Args:
        target_window: The widget to introspect

    Returns:
        Tuple of (get_widget_tree tool, find_widget tool)
    """
    from claude_agent_sdk import tool

    @tool(
        name="get_widget_tree",
        description="Get the complete hierarchical structure of all widgets in the window, including their types, names, properties, and children",
        input_schema={"max_depth": {"type": "number", "description": "Maximum depth to traverse (default 5)", "default": 5}}
    )
    async def get_widget_tree(args: dict) -> dict[str, Any]:
        """
        Get hierarchical widget tree.

        Args:
            args: Dictionary with optional max_depth parameter

        Returns:
            MCP tool result with widget tree JSON
        """
        try:
            max_depth = int(args.get("max_depth", 5))

            print(f"[DEBUG] get_widget_tree: Starting for {target_window}")
            # Run on main thread since Qt widgets can only be accessed from main thread
            tree = run_on_main_thread(serialize_widget_tree, target_window, max_depth)
            print(f"[DEBUG] get_widget_tree: Got tree with {len(str(tree))} chars")

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps(tree, indent=2)
                }]
            }

        except Exception as e:
            import traceback
            return {
                "content": [{"type": "text", "text": f"Widget tree error: {str(e)}\n{traceback.format_exc()}"}],
                "is_error": True
            }

    @tool(
        name="find_widget",
        description="Find a specific widget by its object name and get detailed information about it",
        input_schema={"object_name": {"type": "string", "description": "The objectName of the widget to find"}}
    )
    async def find_widget(args: dict) -> dict[str, Any]:
        """
        Find widget by object name and return its details.

        Args:
            args: Dictionary with object_name parameter

        Returns:
            MCP tool result with widget details or error
        """
        def _find_and_serialize(root, name):
            """Find widget and serialize it (runs on main thread)."""
            widget = find_widget_by_name(root, name)
            if widget is None:
                return None
            return {
                "info": serialize_widget(widget),
                "summary": get_widget_summary(widget)
            }

        try:
            object_name = args.get("object_name")
            if not object_name:
                return {
                    "content": [{"type": "text", "text": "object_name parameter is required"}],
                    "is_error": True
                }

            print(f"[DEBUG] find_widget: Looking for '{object_name}'")
            # Run on main thread since Qt widgets can only be accessed from main thread
            result = run_on_main_thread(_find_and_serialize, target_window, object_name)
            print(f"[DEBUG] find_widget: Found={result is not None}")

            if result is None:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Widget with objectName '{object_name}' not found. Make sure the widget has setObjectName() called on it."
                    }],
                    "is_error": True
                }

            return {
                "content": [{
                    "type": "text",
                    "text": f"{result['summary']}\n\nDetails:\n{json.dumps(result['info'], indent=2)}"
                }]
            }

        except Exception as e:
            import traceback
            return {
                "content": [{"type": "text", "text": f"Find widget error: {str(e)}\n{traceback.format_exc()}"}],
                "is_error": True
            }

    return get_widget_tree, find_widget
