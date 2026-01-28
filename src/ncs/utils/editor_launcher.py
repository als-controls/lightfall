"""Editor launcher utility for opening files in code editors.

Provides functions to open source files at specific line numbers
using URL protocol handlers for VSCode and PyCharm.

URL Formats:
- VSCode: vscode://file/{absolute-path}:{line}:{column}
- PyCharm: jetbrains://pycharm/navigate/reference?project={project}&path={path}:{line}:{column}
  (requires JetBrains Toolbox to register the jetbrains:// protocol)
"""

from __future__ import annotations

import os
import urllib.parse
from enum import Enum
from pathlib import Path

from ncs.utils.logging import logger


class CodeEditor(Enum):
    """Supported code editors."""

    VSCODE = "vscode"
    PYCHARM = "pycharm"


# Protocol names for each editor (used for registry checks)
EDITOR_PROTOCOLS = {
    CodeEditor.VSCODE: "vscode",
    CodeEditor.PYCHARM: "jetbrains",  # JetBrains Toolbox registers this
}

# Markers that indicate a project root directory
PROJECT_MARKERS = [
    ".idea",  # PyCharm/JetBrains project
    ".git",  # Git repository
    "pyproject.toml",  # Python project
    "setup.py",  # Python project
    ".vscode",  # VSCode project
]


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


def is_editor_available(editor: CodeEditor) -> bool:
    """Check if the given editor's protocol handler is available.

    Args:
        editor: The code editor to check.

    Returns:
        True if the editor's protocol is registered, False otherwise.
    """
    protocol = EDITOR_PROTOCOLS.get(editor)
    if protocol is None:
        return False
    return is_protocol_registered(protocol)


def find_project_root(file_path: str) -> Path | None:
    """Find the project root directory for a given file.

    Walks up the directory tree looking for project markers like
    .idea, .git, pyproject.toml, etc.

    Args:
        file_path: Absolute path to a file.

    Returns:
        Path to the project root, or None if not found.
    """
    path = Path(file_path).resolve()

    # Start from the file's parent directory
    current = path.parent if path.is_file() else path

    while current != current.parent:  # Stop at filesystem root
        for marker in PROJECT_MARKERS:
            if (current / marker).exists():
                logger.debug("Found project root at {} (marker: {})", current, marker)
                return current
        current = current.parent

    return None


def get_project_name(file_path: str) -> str:
    """Get the project name for a given file.

    Attempts to find the project root and returns its folder name.
    Falls back to the drive letter or 'unknown' if not found.

    Args:
        file_path: Absolute path to a file.

    Returns:
        Project name string.
    """
    project_root = find_project_root(file_path)
    if project_root is not None:
        return project_root.name

    # Fallback: use first directory component after drive
    path = Path(file_path).resolve()
    parts = path.parts
    if len(parts) > 1:
        return parts[1]  # First directory after drive/root

    return "unknown"


def build_editor_url(
    file_path: str,
    line: int,
    editor: CodeEditor,
    column: int = 1,
    project: str | None = None,
) -> str | None:
    """Build the URL to open a file at a specific location in an editor.

    Args:
        file_path: Absolute path to the file.
        line: Line number to navigate to (1-indexed).
        editor: The code editor to use.
        column: Column number (1-indexed, default 1).
        project: Project name for PyCharm (auto-detected if None).

    Returns:
        The URL string, or None if the editor is not supported.
    """
    # Normalize path separators for URLs (use forward slashes)
    normalized_path = file_path.replace("\\", "/")

    if editor == CodeEditor.VSCODE:
        # VSCode format: vscode://file/{path}:{line}:{column}
        return f"vscode://file/{normalized_path}:{line}:{column}"

    elif editor == CodeEditor.PYCHARM:
        # PyCharm format: jetbrains://pycharm/navigate/reference?project={project}&path={path}:{line}:{column}
        # Requires JetBrains Toolbox to be installed
        if project is None:
            project = get_project_name(file_path)

        # URL-encode the project name and path
        encoded_project = urllib.parse.quote(project, safe="")
        encoded_path = urllib.parse.quote(f"{normalized_path}:{line}:{column}", safe=":/")

        return f"jetbrains://pycharm/navigate/reference?project={encoded_project}&path={encoded_path}"

    else:
        logger.error("Unknown editor: {}", editor)
        return None


def open_in_editor(
    file_path: str,
    line: int,
    editor: CodeEditor,
    column: int = 1,
    project: str | None = None,
) -> bool:
    """Open a file at a specific line in the configured code editor.

    Uses URL protocol handlers to open files:
    - VSCode: vscode://file/{path}:{line}:{column}
    - PyCharm: jetbrains://pycharm/navigate/reference?project={project}&path={path}:{line}:{column}

    Args:
        file_path: Absolute path to the file.
        line: Line number to navigate to (1-indexed).
        editor: The code editor to use.
        column: Column number (1-indexed, default 1).
        project: Project name for PyCharm (auto-detected if None).

    Returns:
        True if the open command was issued successfully, False on error.
    """
    try:
        url = build_editor_url(file_path, line, editor, column, project)
        if url is None:
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
