"""Screenshot tool for capturing Qt window visuals."""

import asyncio
import base64
from typing import Any
from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget
from lucid.claude._internal.threading import run_on_main_thread


def create_screenshot_tool(target_window: QWidget):
    """
    Create a screenshot tool bound to a specific widget.

    Args:
        target_window: The widget to capture screenshots of

    Returns:
        Tool function
    """
    from functools import partial
    from claude_agent_sdk import tool

    @tool(
        name="screenshot",
        description="Capture a screenshot of the Qt window to see its current visual state",
        input_schema={}
    )
    async def take_screenshot(args: dict) -> dict[str, Any]:
        """
        Capture screenshot of the target window.

        Returns:
            MCP tool result with image content
        """
        def _capture_screenshot(window):
            """Capture screenshot (runs on main thread)."""
            # Try using grab() first
            try:
                pixmap = window.grab()
            except Exception:
                # Fallback to render method
                pixmap = QPixmap(window.size())
                window.render(pixmap)

            if not pixmap or pixmap.isNull():
                return None

            # Convert to PNG bytes
            byte_array = QByteArray()
            buffer = QBuffer(byte_array)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            pixmap.save(buffer, "PNG")
            buffer.close()

            # Encode to base64
            return base64.b64encode(byte_array.data()).decode("utf-8")

        try:
            print(f"[DEBUG] Screenshot: Starting capture of {target_window}")
            # Run on main thread since Qt widgets can only be accessed from main thread
            img_data = run_on_main_thread(_capture_screenshot, target_window)
            print(f"[DEBUG] Screenshot: capture complete")

            if img_data is None:
                return {
                    "content": [{"type": "text", "text": "Failed to capture screenshot"}],
                    "is_error": True
                }

            return {
                "content": [{
                    "type": "image",
                    "data": img_data,
                    "mimeType": "image/png"
                }]
            }

        except Exception as e:
            import traceback
            return {
                "content": [{"type": "text", "text": f"Screenshot error: {str(e)}\n{traceback.format_exc()}"}],
                "is_error": True
            }

    return take_screenshot
