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

# Markers that indicate a project root directory (in priority order)
# .idea is checked first as it's the definitive PyCharm project marker
PROJECT_MARKERS_PYCHARM = [".idea"]
PROJECT_MARKERS_OTHER = [".git", "pyproject.toml", "setup.py", ".vscode"]


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


def find_project_root(file_path: str, markers: list[str] | None = None) -> Path | None:
    """Find the project root directory for a given file.

    Walks up the directory tree looking for project markers like
    .idea, .git, pyproject.toml, etc.

    Args:
        file_path: Absolute path to a file.
        markers: List of marker files/dirs to look for. If None, uses default markers.

    Returns:
        Path to the project root, or None if not found.
    """
    if markers is None:
        markers = PROJECT_MARKERS_PYCHARM + PROJECT_MARKERS_OTHER

    path = Path(file_path).resolve()

    # Start from the file's parent directory
    current = path.parent if path.is_file() else path

    while current != current.parent:  # Stop at filesystem root
        for marker in markers:
            if (current / marker).exists():
                logger.debug("Found project root at {} (marker: {})", current, marker)
                return current
        current = current.parent

    return None


def find_pycharm_project_root(file_path: str) -> Path | None:
    """Find the PyCharm project root for a given file.

    First looks for .idea directory (definitive PyCharm marker),
    then falls back to other markers.

    Args:
        file_path: Absolute path to a file.

    Returns:
        Path to the PyCharm project root, or None if not found.
    """
    # First, look specifically for .idea (PyCharm's project marker)
    idea_root = find_project_root(file_path, PROJECT_MARKERS_PYCHARM)
    if idea_root is not None:
        return idea_root

    # Fall back to other markers
    return find_project_root(file_path, PROJECT_MARKERS_OTHER)


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
        # PyCharm format: jetbrains://pycharm/navigate/reference?project={project}&path={relative_path}:{line}:{column}
        # Requires JetBrains Toolbox to be installed
        # Path must be relative to the PyCharm project root (.idea directory)

        project_root = find_pycharm_project_root(file_path)
        if project_root is not None:
            project_name = project if project else project_root.name
            # Make path relative to project root
            try:
                relative_path = Path(file_path).resolve().relative_to(project_root)
                # Use forward slashes for URL
                relative_path_str = str(relative_path).replace("\\", "/")
            except ValueError:
                # File is not under project root, use absolute path as fallback
                relative_path_str = normalized_path
        else:
            # No project root found, use absolute path and derive project name
            project_name = project if project else get_project_name(file_path)
            relative_path_str = normalized_path

        # URL-encode the project name and path (encode slashes too, like PyCharm does)
        encoded_project = urllib.parse.quote(project_name, safe="")
        # PyCharm URL-encodes the path including slashes (%2F)
        encoded_path = urllib.parse.quote(relative_path_str, safe="")

        return f"jetbrains://pycharm/navigate/reference?project={encoded_project}&path={encoded_path}:{line}:{column}"

    else:
        logger.error("Unknown editor: {}", editor)
        return None


def open_via_pycharm_http(file_path: str, line: int, column: int = 1) -> bool:
    """Open a file in PyCharm using its built-in HTTP REST API.

    PyCharm runs a built-in HTTP server on port 63342 that can be used
    to open files directly. This is more reliable than the jetbrains://
    URL scheme as it doesn't require JetBrains Toolbox.

    Args:
        file_path: Absolute path to the file.
        line: Line number to navigate to (1-indexed).
        column: Column number (1-indexed, default 1).

    Returns:
        True if successful, False on error.
    """
    try:
        import urllib.request
        import urllib.error

        # Normalize path (use forward slashes)
        normalized_path = file_path.replace("\\", "/")

        # PyCharm HTTP API format: http://localhost:63342/api/file/{path}:{line}:{column}
        url = f"http://localhost:63342/api/file/{normalized_path}:{line}:{column}"

        logger.debug("Opening via PyCharm HTTP API: {}", url)

        # Make the request with a short timeout
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2) as response:
            logger.debug("PyCharm HTTP API response: {}", response.status)
            return response.status == 200

    except urllib.error.URLError as e:
        logger.debug("PyCharm HTTP API not available: {}", e)
        return False
    except Exception as e:
        logger.error("Failed to open via PyCharm HTTP API: {}", e)
        return False


def open_in_editor(
    file_path: str,
    line: int,
    editor: CodeEditor,
    column: int = 1,
    project: str | None = None,
    use_http_fallback: bool = True,
) -> bool:
    """Open a file at a specific line in the configured code editor.

    Uses URL protocol handlers to open files:
    - VSCode: vscode://file/{path}:{line}:{column}
    - PyCharm: jetbrains://pycharm/navigate/reference?project={project}&path={path}:{line}:{column}

    For PyCharm, if the jetbrains:// URL doesn't work, falls back to using
    PyCharm's built-in HTTP REST API on localhost:63342.

    Args:
        file_path: Absolute path to the file.
        line: Line number to navigate to (1-indexed).
        editor: The code editor to use.
        column: Column number (1-indexed, default 1).
        project: Project name for PyCharm (auto-detected if None).
        use_http_fallback: For PyCharm, try HTTP API first (more reliable).

    Returns:
        True if the open command was issued successfully, False on error.
    """
    # For PyCharm, try the HTTP API first as it's more reliable
    if editor == CodeEditor.PYCHARM and use_http_fallback:
        if open_via_pycharm_http(file_path, line, column):
            return True
        logger.debug("HTTP API failed, falling back to jetbrains:// URL")

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
