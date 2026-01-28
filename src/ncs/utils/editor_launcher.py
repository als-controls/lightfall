"""Editor launcher utility for opening files in code editors.

Provides functions to open source files at specific line numbers
using URL protocol handlers for VSCode and PyCharm.
"""

from __future__ import annotations

import os
from enum import Enum

from ncs.utils.logging import logger


class CodeEditor(Enum):
    """Supported code editors."""

    VSCODE = "vscode"
    PYCHARM = "pycharm"


def is_protocol_registered(protocol: str) -> bool:
    """Check if a URL protocol is registered in Windows registry.

    Args:
        protocol: The protocol name (e.g., "vscode", "jetbrains").

    Returns:
        True if the protocol is registered, False otherwise.
    """
    try:
        import winreg

        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, protocol)
        return True
    except FileNotFoundError:
        return False
    except ImportError:
        # Not on Windows, assume protocol handlers work
        logger.debug("winreg not available, assuming protocol {} is registered", protocol)
        return True
    except Exception as e:
        logger.warning("Error checking protocol {}: {}", protocol, e)
        return False


def open_in_editor(file_path: str, line: int, editor: CodeEditor) -> bool:
    """Open a file at a specific line in the configured code editor.

    Uses URL protocol handlers to open files:
    - VSCode: vscode://file/{path}:{line}:1
    - PyCharm: jetbrains://pycharm/navigate/reference?path={path}&line={line}

    Args:
        file_path: Absolute path to the file.
        line: Line number to navigate to (1-indexed).
        editor: The code editor to use.

    Returns:
        True if the open command was issued successfully, False on error.
    """
    try:
        # Normalize path separators for URLs
        normalized_path = file_path.replace("\\", "/")

        if editor == CodeEditor.VSCODE:
            url = f"vscode://file/{normalized_path}:{line}:1"
        elif editor == CodeEditor.PYCHARM:
            url = f"jetbrains://pycharm/navigate/reference?path={normalized_path}&line={line}"
        else:
            logger.error("Unknown editor: {}", editor)
            return False

        logger.debug("Opening in {}: {}", editor.value, url)
        os.startfile(url)
        return True

    except Exception as e:
        logger.error("Failed to open {} in {}: {}", file_path, editor.value, e)
        return False


def get_editor_from_string(editor_str: str) -> CodeEditor | None:
    """Convert a string to a CodeEditor enum value.

    Args:
        editor_str: Editor name string ("vscode" or "pycharm").

    Returns:
        CodeEditor enum value or None if invalid.
    """
    try:
        return CodeEditor(editor_str.lower())
    except ValueError:
        return None
