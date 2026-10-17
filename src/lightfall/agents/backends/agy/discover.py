"""Locate the agy executable, check its sign-in, and pick a console Python.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

_SIGNIN_TIMEOUT_S = 30


def console_python() -> str:
    """``sys.executable``, but a console ``python.exe`` instead of ``pythonw.exe``.

    agy runs the hook command and reads its stdout; a GUI-subsystem interpreter
    has no usable std handles, so the hook must use the console sibling.
    """
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        sibling = exe.with_name("python.exe")
        if sibling.exists():
            return str(sibling)
    return str(exe)


def _get_short_path_name(path: str) -> str:
    """Windows 8.3 form of ``path`` (the long path if unavailable)."""
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    buf = ctypes.create_unicode_buffer(32768)
    n = kernel32.GetShortPathNameW(path, buf, len(buf))
    return buf.value if 0 < n < len(buf) else path


_CMD_UNSAFE = set("&^%()!;,='\"<>|")


def _cmd_unsafe(text: str) -> bool:
    return any(c.isspace() or c in _CMD_UNSAFE for c in text)


def _hook_path(path: str | Path) -> str:
    text = str(Path(path).resolve())
    if sys.platform == "win32":
        if _cmd_unsafe(text):
            text = _get_short_path_name(text)
            if _cmd_unsafe(Path(text).as_posix()):
                raise ValueError(
                    f"agy hook command path contains characters unsafe for cmd ({text}); "
                    "move the Python environment or enable 8.3 short names")
        return Path(text).as_posix()
    return shlex.quote(text)


def hook_command(python: str | Path, script: str | Path) -> str:
    """Command line agy runs for the PreToolUse hook.

    agy runs it through cmd and mangles double quotes, so Windows paths are
    emitted unquoted with forward slashes (8.3 short names if they have spaces).
    """
    return f"{_hook_path(python)} -I {_hook_path(script)}"


def find_agy(pref_path: str | None) -> str | None:
    """The agy executable: the pref path if it exists, then PATH, then the default install."""
    if pref_path and Path(pref_path).is_file():
        return str(pref_path)
    found = shutil.which("agy")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        default = Path(local) / "agy" / "bin" / "agy.exe"
        if default.is_file():
            return str(default)
    return None


def _tail(text: str, n: int = 5) -> str:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def agy_models(agy: str | list[str], home: Path) -> tuple[list[tuple[str, str]] | None, str]:
    """Run ``agy models`` against ``home``: ``(rows, detail)``; rows is None on failure.

    Output format (agy 1.3.5): a ``Fetching available models...`` line, then one
    ``<id>	<Display Name>`` row per model -- only TAB-bearing lines count.
    """
    argv = [agy] if isinstance(agy, str) else list(agy)
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            [*argv, "models"], env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_SIGNIN_TIMEOUT_S,
            stdin=subprocess.DEVNULL, creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        return None, f"agy models timed out after {_SIGNIN_TIMEOUT_S} s"
    except OSError as e:
        return None, f"could not run agy: {e}"
    rows = []
    for ln in (proc.stdout or "").splitlines():
        if "\t" in ln and ln.strip():
            model_id, _, label = ln.strip().partition("\t")
            rows.append((model_id.strip(), label.strip() or model_id.strip()))
    if proc.returncode == 0 and rows:
        return rows, f"{len(rows)} models"
    return None, _tail(proc.stderr) or _tail(proc.stdout) or f"agy models exited {proc.returncode}"


def agy_signed_in(agy: str | list[str], home: Path) -> tuple[bool, str]:
    """``(True, "N models")`` when ``agy models`` lists any (see ``agy_models``)."""
    rows, detail = agy_models(agy, home)
    return rows is not None, detail
