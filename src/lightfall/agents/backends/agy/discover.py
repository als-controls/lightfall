"""Locate the agy executable, check its sign-in, and pick a console Python.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

import os
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


def agy_signed_in(agy: str | list[str], home: Path) -> tuple[bool, str]:
    """Run ``agy models`` against ``home``; ``(True, "N models")`` when it lists any.

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
        return False, f"agy models timed out after {_SIGNIN_TIMEOUT_S} s"
    except OSError as e:
        return False, f"could not run agy: {e}"
    models = [ln for ln in (proc.stdout or "").splitlines() if "\t" in ln and ln.strip()]
    if proc.returncode == 0 and models:
        return True, f"{len(models)} models"
    return False, _tail(proc.stderr) or _tail(proc.stdout) or f"agy models exited {proc.returncode}"
