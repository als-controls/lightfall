"""Widget interaction tools (clicking, typing, etc.)."""

import asyncio
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QPushButton, QLineEdit, QTextEdit, QAbstractButton
from PySide6.QtTest import QTest
from lucid.claude._internal.serialization import find_widget_by_name
from lucid.claude._internal.threading import run_on_main_thread


def create_interaction_tools(target_window: QWidget):
    """
    Create interaction tools bound to a specific widget.

    Args:
        target_window: The widget whose children to interact with

    Returns:
        Tuple of (click_widget tool, type_text tool)
    """
    from claude_agent_sdk import tool

    @tool(
        name="click_widget",
        description="Click a widget (button, checkbox, etc.) by its object name to trigger its action",
        input_schema={"object_name": {"type": "string", "description": "The objectName of the widget to click"}}
    )
    async def click_widget(args: dict) -> dict[str, Any]:
        """
        Click a widget by object name.

        Args:
            args: Dictionary with object_name parameter

        Returns:
            MCP tool result indicating success or failure
        """
        def _do_click(root, name):
            """Find and click widget (runs on main thread)."""
            widget = find_widget_by_name(root, name)

            if not widget:
                return {"error": f"Widget '{name}' not found"}

            if not widget.isEnabled():
                return {"error": f"Widget '{name}' is disabled"}

            if not widget.isVisible():
                return {"error": f"Widget '{name}' is not visible"}

            # Click based on widget type
            widget_type = type(widget).__name__

            if isinstance(widget, QAbstractButton):
                # Use direct click() for buttons
                widget.click()
            else:
                # Use QTest for other widgets
                QTest.mouseClick(widget, Qt.MouseButton.LeftButton)

            return {"success": True, "widget_type": widget_type}

        try:
            object_name = args.get("object_name")
            if not object_name:
                return {
                    "content": [{"type": "text", "text": "object_name parameter is required"}],
                    "is_error": True
                }

            print(f"[DEBUG] click_widget: Looking for '{object_name}'")
            # Run on main thread since Qt widgets can only be accessed from main thread
            result = run_on_main_thread(_do_click, target_window, object_name)

            if "error" in result:
                return {
                    "content": [{"type": "text", "text": result["error"]}],
                    "is_error": True
                }

            return {
                "content": [{
                    "type": "text",
                    "text": f"Successfully clicked {result['widget_type']} '{object_name}'"
                }]
            }

        except Exception as e:
            import traceback
            return {
                "content": [{"type": "text", "text": f"Click widget error: {str(e)}\n{traceback.format_exc()}"}],
                "is_error": True
            }

    @tool(
        name="type_text",
        description="Enter text into a text input widget (QLineEdit, QTextEdit) by its object name",
        input_schema={
            "object_name": {"type": "string", "description": "The objectName of the text widget"},
            "text": {"type": "string", "description": "The text to enter"}
        }
    )
    async def type_text(args: dict) -> dict[str, Any]:
        """
        Type text into a text input widget.

        Args:
            args: Dictionary with object_name and text parameters

        Returns:
            MCP tool result indicating success or failure
        """
        def _do_type(root, name, text_value):
            """Find widget and type text (runs on main thread)."""
            widget = find_widget_by_name(root, name)

            if not widget:
                return {"error": f"Widget '{name}' not found"}

            if not widget.isEnabled():
                return {"error": f"Widget '{name}' is disabled"}

            widget_type = type(widget).__name__

            # Type based on widget type
            if isinstance(widget, QLineEdit):
                widget.setText(str(text_value))
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(str(text_value))
            else:
                return {"error": f"Widget '{name}' is not a text input (type: {widget_type})"}

            return {"success": True, "widget_type": widget_type}

        try:
            object_name = args.get("object_name")
            text = args.get("text")

            if not object_name:
                return {
                    "content": [{"type": "text", "text": "object_name parameter is required"}],
                    "is_error": True
                }

            if text is None:
                return {
                    "content": [{"type": "text", "text": "text parameter is required"}],
                    "is_error": True
                }

            print(f"[DEBUG] type_text: Looking for '{object_name}'")
            # Run on main thread since Qt widgets can only be accessed from main thread
            result = run_on_main_thread(_do_type, target_window, object_name, text)

            if "error" in result:
                return {
                    "content": [{"type": "text", "text": result["error"]}],
                    "is_error": True
                }

            return {
                "content": [{
                    "type": "text",
                    "text": f"Successfully entered text into {result['widget_type']} '{object_name}'"
                }]
            }

        except Exception as e:
            import traceback
            return {
                "content": [{"type": "text", "text": f"Type text error: {str(e)}\n{traceback.format_exc()}"}],
                "is_error": True
            }

    return click_widget, type_text
