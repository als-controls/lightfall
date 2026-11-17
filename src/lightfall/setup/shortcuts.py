"""Desktop and Start Menu / app-menu shortcuts for the Shortcut setup step. No Qt.

Windows writes ``Lightfall.lnk`` (WScript.Shell COM via pywin32, else PowerShell) into the
Desktop and Start Menu Programs known folders, so OneDrive-redirected desktops work.
Linux writes ``lightfall.desktop`` to the XDG desktop dir and ~/.local/share/applications.
Other platforms (macOS) are unsupported: the step is not registered there.

Shortcuts always have the same file name, so creating one again overwrites it in place.
"""
from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files
from pathlib import Path

APP_NAME = "Lightfall"
DESCRIPTION = "Lightfall beamline controls and data acquisition"
GUI_SCRIPT = "lightfall-gui"


class Location(Enum):
    DESKTOP = "desktop"
    MENU = "menu"  # Start Menu on Windows, the applications menu on Linux


class ShortcutError(Exception):
    """A shortcut could not be written."""


@dataclass(frozen=True)
class Launcher:
    target: Path
    working_dir: Path
    icon: Path


def is_supported_platform() -> bool:
    return sys.platform == "win32" or sys.platform.startswith("linux")


def menu_label() -> str:
    return "Start Menu" if sys.platform == "win32" else "applications menu"


# --- what the shortcut launches ------------------------------------------------------

def _scripts_dirs() -> list[Path]:
    dirs = [Path(sys.executable).parent]
    scripts = sysconfig.get_path("scripts")
    if scripts and Path(scripts) not in dirs:
        dirs.append(Path(scripts))
    return dirs


def find_gui_script() -> Path | None:
    """The ``lightfall-gui`` gui-script next to this interpreter (no console window)."""
    name = GUI_SCRIPT + (".exe" if sys.platform == "win32" else "")
    for directory in _scripts_dirs():
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def install_root() -> Path:
    """The directory holding the venv (``~/lightfall`` for ``~/lightfall/.venv``)."""
    if sys.prefix != sys.base_prefix:
        return Path(sys.prefix).parent
    return Path.home()


def icon_path() -> Path:
    name = "icon.ico" if sys.platform == "win32" else "icon.png"
    return Path(str(files("lightfall.resources.images") / name))


def launcher() -> Launcher | None:
    target = find_gui_script()
    if target is None:
        return None
    return Launcher(target=target, working_dir=install_root(), icon=icon_path())


# --- where shortcuts go -----------------------------------------------------------

# Known-folder GUIDs (KnownFolders.h).
_FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
_FOLDERID_PROGRAMS = "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}"


def _known_folder(guid: str) -> Path:
    """SHGetKnownFolderPath: honours folder redirection (OneDrive Desktop)."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    folder_id = GUID()
    ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid), ctypes.byref(folder_id))
    buffer = ctypes.c_wchar_p()
    shell32 = ctypes.windll.shell32
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                             wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.HRESULT
    try:
        shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(buffer))
        return Path(buffer.value or "")
    finally:
        ctypes.windll.ole32.CoTaskMemFree(buffer)


def _xdg_desktop_dir() -> Path:
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True,
                             timeout=5, check=True).stdout.strip()
        if out:
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Desktop"


def shortcut_path(location: Location) -> Path:
    if sys.platform == "win32":
        guid = _FOLDERID_DESKTOP if location is Location.DESKTOP else _FOLDERID_PROGRAMS
        return _known_folder(guid) / f"{APP_NAME}.lnk"
    if location is Location.DESKTOP:
        return _xdg_desktop_dir() / "lightfall.desktop"
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return data_home / "applications" / "lightfall.desktop"


def shortcut_exists(location: Location) -> bool:
    try:
        return shortcut_path(location).exists()
    except OSError:
        return False


# --- writing -------------------------------------------------------------------------

def desktop_entry(launch: Launcher) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Comment={DESCRIPTION}\n"
        f'Exec="{launch.target}"\n'
        f"Path={launch.working_dir}\n"
        f"Icon={launch.icon}\n"
        "Terminal=false\n"
        "Categories=Science;Physics;\n"
    )


def _write_lnk(path: Path, launch: Launcher) -> None:
    try:
        import win32com.client  # pywin32 (pulled in by mcp on Windows)
    except ImportError:
        _write_lnk_powershell(path, launch)
        return
    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortcut(str(path))
    link.TargetPath = str(launch.target)
    link.WorkingDirectory = str(launch.working_dir)
    link.IconLocation = f"{launch.icon},0"
    link.Description = DESCRIPTION
    link.Save()


def _ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _write_lnk_powershell(path: Path, launch: Launcher) -> None:
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
        f"{_ps_quote(path)}); "
        f"$s.TargetPath = {_ps_quote(launch.target)}; "
        f"$s.WorkingDirectory = {_ps_quote(launch.working_dir)}; "
        f"$s.IconLocation = {_ps_quote(f'{launch.icon},0')}; "
        f"$s.Description = {_ps_quote(DESCRIPTION)}; "
        "$s.Save()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise ShortcutError(result.stderr.strip() or "PowerShell couldn't save the shortcut")


def create_shortcut(location: Location, launch: Launcher) -> Path:
    """Write (or overwrite) the shortcut at ``location``; raises ShortcutError."""
    try:
        path = shortcut_path(location)
        path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            _write_lnk(path, launch)
        else:
            path.write_text(desktop_entry(launch), encoding="utf-8")
            path.chmod(0o755)  # desktop launchers must be executable to be trusted
    except ShortcutError:
        raise
    except Exception as exc:  # noqa: BLE001 - COM, ctypes and OS errors alike
        raise ShortcutError(str(exc)) from exc
    return path
