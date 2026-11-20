"""Reinstalls deferred until Lightfall exits, run by a detached helper.

``apply_updates`` records a :class:`PendingInstall` for each source checkout
whose ``pyproject.toml`` changed instead of running ``pip install -e`` in the
live process. :func:`schedule` registers them with :mod:`lightfall.utils.restart`.
When Lightfall quits, normally or to restart, it starts
:mod:`lightfall.updates._install_helper` from a temp copy. The helper waits for
this process to exit, reinstalls, checks that each package imports, and only
then relaunches Lightfall if a restart was requested.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from lightfall.updates import _install_helper
from lightfall.utils.logging import logger


@dataclass(frozen=True)
class PendingInstall:
    """A ``pip install -e root`` to run after exit, and the dists it must leave importable."""

    root: Path
    dists: tuple[str, ...]


_PENDING: dict[Path, PendingInstall] = {}
_LOCK = threading.Lock()


def import_names(dist: str) -> list[str]:
    """Top-level module to import-check for ``dist``: its normalized name, if that module exists.

    Editable installs carry no ``top_level.txt``, so this guesses from the name
    and checks the guess against the running process. If no such module
    exists, only the dist's metadata is checked.
    """
    name = dist.replace("-", "_").replace(".", "_")
    try:
        found = importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        found = False
    return [name] if found else []


def schedule(installs: list[PendingInstall]) -> None:
    """Queue ``installs`` to run once Lightfall exits."""
    if not installs:
        return
    from lightfall.utils import restart

    with _LOCK:
        for item in installs:
            _PENDING[Path(item.root)] = item
    for item in installs:
        logger.info("[update] pip install -e {} deferred until Lightfall exits", item.root)
    restart.set_exit_command(exit_command)


def pending() -> list[PendingInstall]:
    with _LOCK:
        return list(_PENDING.values())


def clear() -> None:
    with _LOCK:
        _PENDING.clear()


def log_dir() -> Path:
    return Path.home() / "lightfall" / "logs" / "updates"


def manual_command(python: str, root: Path | str) -> str:
    import subprocess

    return subprocess.list2cmdline(_install_helper.pip_install_command(python, str(root)))


def exit_command(
    relaunch: list[str] | None,
    python: str = sys.executable,
    pid: int | None = None,
    workdir: Path | None = None,
    logs: Path | None = None,
) -> list[str] | None:
    """The helper command for the pending installs, or None if there are none.

    Copies the helper script and writes its spec into ``workdir`` (a new temp
    dir by default), so the helper imports nothing from the package it
    reinstalls.
    """
    with _LOCK:
        installs = list(_PENDING.values())
        _PENDING.clear()
    if not installs:
        return None
    workdir = Path(workdir or tempfile.mkdtemp(prefix="lightfall-update-"))
    logs = Path(logs or log_dir())
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"update-{time.strftime('%Y%m%d-%H%M%S')}.log"
    script = workdir / "lightfall_update_helper.py"
    shutil.copyfile(_install_helper.__file__, script)
    spec = {
        "pid": os.getpid() if pid is None else pid,
        "python": python,
        "installs": [
            {"root": str(item.root), "dists": list(item.dists),
             "modules": {d: import_names(d) for d in item.dists}}
            for item in installs
        ],
        "relaunch": relaunch,
        "cwd": os.getcwd(),
        "log": str(log_path),
        "grace": 1.0,
    }
    spec_path = workdir / "spec.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    logger.info("[update] reinstall helper will log to {}", log_path)
    return [python, "-I", str(script), str(spec_path)]
