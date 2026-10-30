"""Apply updates: fast-forward source checkouts and pip-upgrade index installs.

Assumes one Lightfall process per venv. Another instance sharing the same venv
or checkouts is not detected, so its scans are not guarded; this goes away once
the RunEngine runs as a separate service.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from lightfall.updates.git import (
    NO_WINDOW,
    GitError,
    RepoState,
    pull_ff,
    pyproject_changed,
    read_repo_state,
)
from lightfall.updates.pypi import PypiState


class UpdateBlocked(Exception):
    """Updating is not allowed right now (e.g. a scan is running)."""


@dataclass
class ItemResult:
    """The outcome for one repo or package."""

    name: str
    ok: bool
    changed: bool
    message: str
    output: str = ""


def scan_running() -> bool:
    """Whether this process's engine is running or has queued work.

    Reads the existing engine without creating one; no engine means no scan.
    """
    import lightfall.acquire.engine as engine_mod

    engine = engine_mod._engine
    if engine is None:
        return False
    return not engine.is_idle or engine.queue_size > 0


def _pip(cmd: list[str], run: Callable[..., subprocess.CompletedProcess],
         log: Callable[[str], None]) -> tuple[bool, str]:
    log("$ " + " ".join(cmd))
    try:
        proc = run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                   creationflags=NO_WINDOW)
    except OSError as e:
        log(str(e))
        return False, str(e)
    output = (proc.stdout or "") + (proc.stderr or "")
    if output.strip():
        log(output.rstrip())
    return proc.returncode == 0, output


def _apply_repo(state: RepoState, python: str, run: Callable[..., subprocess.CompletedProcess],
                log: Callable[[str], None]) -> ItemResult:
    try:
        current = read_repo_state(state.root, state.dists, fetch=False)
    except GitError as e:
        return ItemResult(state.name, False, False, "could not read repo", str(e))
    if current.blockers:
        return ItemResult(state.name, False, False, current.blockers[0])
    log(f"$ git -C {state.root} pull --ff-only {current.remote} {current.default_branch}")
    try:
        old, new = pull_ff(current)
    except GitError as e:
        log(str(e))
        return ItemResult(state.name, False, False, "pull failed", str(e))
    changed = old != new
    try:
        reinstall = changed and pyproject_changed(state.root, old, new)
    except GitError as e:
        log(str(e))
        return ItemResult(state.name, False, True, "reinstall check failed", str(e))
    if reinstall:
        ok, output = _pip([python, "-m", "pip", "install", "-e", str(state.root)], run, log)
        if not ok:
            return ItemResult(state.name, False, True, "reinstall failed", output)
    return ItemResult(state.name, True, changed, "updated" if changed else "already current")


def apply_updates(
    repos: list[RepoState],
    pypi: list[PypiState],
    python: str = sys.executable,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    log: Callable[[str], None] = lambda s: None,
) -> list[ItemResult]:
    """Update the given repos and packages; one result per item, in input order.

    Blocking (run it off the GUI thread). Per-item failures are reported in the
    results, never raised.

    Raises:
        UpdateBlocked: if a scan is running.
    """
    if scan_running():
        raise UpdateBlocked("a scan is running")
    results = [_apply_repo(state, python, run, log) for state in repos]
    if pypi:
        names = [p.name for p in pypi]
        ok, output = _pip([python, "-m", "pip", "install", "--upgrade", *names], run, log)
        message = "updated" if ok else "pip upgrade failed"
        results.extend(ItemResult(name, ok, ok, message, output) for name in names)
    return results
