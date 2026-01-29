"""Editor launcher utility for opening files in code editors.

Provides functions to open source files at specific line numbers
using direct CLI invocation (preferred), HTTP API, or URL protocol handlers.

Opening Methods (in priority order):
1. CLI: Direct executable invocation (most reliable, doesn't need project context)
   - PyCharm: pycharm64.exe --line <line> [--column <col>] <file>
   - VSCode: code -g <file>:<line>:<column>
2. HTTP API: PyCharm's built-in REST API on localhost:63342
3. URL Protocol: vscode:// or jetbrains:// URL schemes (requires registration)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.parse
from enum import Enum
from pathlib import Path

from lucid.utils.logging import logger


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


def find_pycharm_executable() -> Path | None:
    """Find the PyCharm executable on Windows.

    Searches in order:
    1. JetBrains Toolbox 2.0+ locations (LOCALAPPDATA/Programs)
    2. Standalone installs in Program Files
    3. PATH environment variable

    Returns:
        Path to the PyCharm executable, or None if not found.
    """
    # Check Toolbox 2.0+ locations first
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        for edition in ["PyCharm Professional", "PyCharm Community"]:
            exe = Path(localappdata) / "Programs" / edition / "bin" / "pycharm64.exe"
            if exe.exists():
                logger.debug("Found PyCharm at Toolbox location: {}", exe)
                return exe

    # Check Program Files for standalone installs
    for prog_dir in [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")]:
        if prog_dir:
            jetbrains_dir = Path(prog_dir) / "JetBrains"
            if jetbrains_dir.exists():
                # Sort to get newest version first (PyCharm 2024.1 > PyCharm 2023.3)
                pycharm_dirs = sorted(jetbrains_dir.glob("PyCharm*"), reverse=True)
                for pycharm_dir in pycharm_dirs:
                    exe = pycharm_dir / "bin" / "pycharm64.exe"
                    if exe.exists():
                        logger.debug("Found PyCharm at standalone location: {}", exe)
                        return exe

    # Check PATH
    for name in ["pycharm64", "pycharm"]:
        exe = shutil.which(name)
        if exe:
            logger.debug("Found PyCharm in PATH: {}", exe)
            return Path(exe)

    logger.debug("PyCharm executable not found")
    return None


def find_vscode_executable() -> Path | None:
    """Find the VSCode executable.

    Checks the PATH for the 'code' command.

    Returns:
        Path to the VSCode executable, or None if not found.
    """
    exe = shutil.which("code")
    if exe:
        logger.debug("Found VSCode in PATH: {}", exe)
        return Path(exe)

    logger.debug("VSCode executable not found")
    return None


def open_via_cli(file_path: str, line: int, editor: CodeEditor, column: int = 1) -> bool:
    """Open a file using direct CLI invocation.

    This is the most reliable method as it doesn't require project context
    or URL protocol registration.

    Args:
        file_path: Absolute path to the file.
        line: Line number to navigate to (1-indexed).
        editor: The code editor to use.
        column: Column number (1-indexed, default 1).

    Returns:
        True if the editor was launched successfully, False otherwise.
    """
    if editor == CodeEditor.PYCHARM:
        exe = find_pycharm_executable()
        if exe is None:
            return False

        # PyCharm CLI: pycharm64.exe --line <line> [--column <col>] <file>
        args = [str(exe), "--line", str(line)]
        if column > 1:
            args.extend(["--column", str(column)])
        args.append(file_path)

    elif editor == CodeEditor.VSCODE:
        exe = find_vscode_executable()
        if exe is None:
            return False

        # VSCode CLI: code -g <file>:<line>:<column>
        args = [str(exe), "-g", f"{file_path}:{line}:{column}"]

    else:
        logger.error("CLI launch not supported for editor: {}", editor)
        return False

    try:
        logger.debug("Opening via CLI: {}", " ".join(args))
        # Use DETACHED_PROCESS to not block and not create a console window
        subprocess.Popen(
            args,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:
        logger.error("Failed to open via CLI: {}", e)
        return False


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

    Tries multiple methods in order of reliability:
    1. CLI: Direct executable invocation (most reliable, doesn't need project context)
    2. HTTP API: PyCharm's built-in REST API on localhost:63342
    3. URL Protocol: vscode:// or jetbrains:// URL schemes

    Args:
        file_path: Absolute path to the file.
        line: Line number to navigate to (1-indexed).
        editor: The code editor to use.
        column: Column number (1-indexed, default 1).
        project: Project name for PyCharm (auto-detected if None).
        use_http_fallback: For PyCharm, try HTTP API as second fallback.

    Returns:
        True if the open command was issued successfully, False on error.
    """
    # Try CLI first (most reliable, doesn't need project context)
    if open_via_cli(file_path, line, editor, column):
        return True
    logger.debug("CLI launch failed, trying fallback methods")

    # Fall back to HTTP API for PyCharm
    if editor == CodeEditor.PYCHARM and use_http_fallback:
        if open_via_pycharm_http(file_path, line, column):
            return True
        logger.debug("HTTP API failed, falling back to jetbrains:// URL")

    # Last resort: URL protocol
    try:
        url = build_editor_url(file_path, line, editor, column, project)
        if url is None:
            return False

        logger.debug("Opening via URL protocol in {}: {}", editor.value, url)
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
