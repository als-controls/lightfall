"""Screenshot tool for capturing Qt window visuals."""

import base64
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget

from lightfall.claude._internal.threading import run_on_main_thread

# Claude's vision pipeline downsamples anything larger anyway (~1.5k px useful
# ceiling), and an unscaled hi-DPI window PNG can exceed the SDK transport's
# JSON message buffer as one giant base64 tool_result line.
MAX_SCREENSHOT_DIM = 1568


def capture_screenshot_b64(window: QWidget) -> str | None:
    """Grab `window`, downscale to MAX_SCREENSHOT_DIM, return base64 PNG.

    Must run on the GUI thread (Qt widget access).
    """
    try:
        pixmap = window.grab()
    except Exception:
        pixmap = QPixmap(window.size())
        window.render(pixmap)

    if not pixmap or pixmap.isNull():
        return None

    if max(pixmap.width(), pixmap.height()) > MAX_SCREENSHOT_DIM:
        from PySide6.QtCore import Qt
        pixmap = pixmap.scaled(
            MAX_SCREENSHOT_DIM, MAX_SCREENSHOT_DIM,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return base64.b64encode(byte_array.data()).decode("utf-8")


def create_screenshot_tool(target_window: QWidget):
    """
    Create a screenshot tool bound to a specific widget.

    Args:
        target_window: The widget to capture screenshots of

    Returns:
        Tool function
    """
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
        try:
            print(f"[DEBUG] Screenshot: Starting capture of {target_window}")
            # Run on main thread since Qt widgets can only be accessed from main thread
            img_data = run_on_main_thread(capture_screenshot_b64, target_window)
            print("[DEBUG] Screenshot: capture complete")

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
