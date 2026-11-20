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

from lightfall.updates._install_helper import pip_install_command, verify_command
from lightfall.updates.deferred import PendingInstall, import_names, manual_command
from lightfall.updates.git import (
    NO_WINDOW,
    GitError,
    RepoState,
    pull_ff,
    pyproject_changed,
    read_repo_state,
)
from lightfall.updates.pypi import PypiState
from lightfall.utils.logging import logger

Log = Callable[[str], None]
Run = Callable[..., subprocess.CompletedProcess]


class UpdateBlocked(Exception):
    """Updating is not allowed right now (e.g. a scan is running)."""


@dataclass
class ItemResult:
    """The outcome for one repo or package.

    ``changed`` means code on disk changed. Whether a restart is safe is a
    separate question, answered by :attr:`restart_safe`. ``install_failed``
    means pip or the post-install import check failed, so the environment may
    be broken; ``manual_fix`` is the command that repairs it.
    """

    name: str
    ok: bool
    changed: bool
    message: str
    output: str = ""
    install_failed: bool = False
    manual_fix: str = ""
    pending_install: PendingInstall | None = None

    @property
    def restart_safe(self) -> bool:
        return self.ok and not self.install_failed


def scan_running() -> bool:
    """Whether this process's engine is running or has queued work.

    Reads the existing engine without creating one; no engine means no scan.
    """
    import lightfall.acquire.engine as engine_mod

    engine = engine_mod._engine
    if engine is None:
        return False
    return not engine.is_idle or engine.queue_size > 0


def _tee(log: Log) -> Log:
    """Send each updater line to ``log`` (the dialog) and to the app log, which persists."""

    def emit(line: str) -> None:
        logger.info("[update] {}", line)
        log(line)

    return emit


def _run(cmd: list[str], run: Run, log: Log) -> tuple[bool, str]:
    log("$ " + subprocess.list2cmdline(cmd))
    try:
        proc = run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                   creationflags=NO_WINDOW)
    except OSError as e:
        log(str(e))
        return False, str(e)
    output = (proc.stdout or "") + (proc.stderr or "")
    if output.strip():
        log(output.rstrip())
    if proc.returncode != 0:
        log(f"exit code {proc.returncode}")
    return proc.returncode == 0, output


def _verify(python: str, dists: tuple[str, ...] | list[str], run: Run,
            log: Log) -> tuple[bool, str]:
    """Check in a fresh interpreter that each dist's metadata exists and its module imports."""
    outputs = []
    for dist in dists:
        ok, output = _run(verify_command(python, dist, import_names(dist)), run, log)
        outputs.append(output)
        if not ok:
            log(f"{dist} does not import after the install")
            return False, "\n".join(outputs)
    return True, "\n".join(outputs)


def _join(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


def _apply_repo(state: RepoState, python: str, run: Run, log: Log,
                defer_install: bool) -> ItemResult:
    try:
        current = read_repo_state(state.root, state.dists, fetch=False)
    except GitError as e:
        log(str(e))
        return ItemResult(state.name, False, False, "could not read repo", str(e))
    if current.blockers:
        log(f"{state.name}: not updated: {current.blockers[0]}")
        return ItemResult(state.name, False, False, current.blockers[0])
    log(f"$ git -C {state.root} pull --ff-only {current.remote} {current.default_branch}")
    try:
        old, new = pull_ff(current)
    except GitError as e:
        log(str(e))
        return ItemResult(state.name, False, False, "pull failed", str(e))
    changed = old != new
    log(f"{state.name}: {old[:10]} -> {new[:10]}" if changed else f"{state.name}: already current")
    try:
        reinstall = changed and pyproject_changed(state.root, old, new)
    except GitError as e:
        log(str(e))
        return ItemResult(state.name, False, True, "reinstall check failed", str(e))
    if not reinstall:
        return ItemResult(state.name, True, changed, "updated" if changed else "already current")
    fix = manual_command(python, state.root)
    if defer_install:
        # Running pip from inside the live process fails on Windows (in-use
        # launchers/files) and can leave the venv half-uninstalled; run it after exit.
        log(f"{state.name}: pyproject.toml changed; '{fix}' will run after Lightfall exits")
        return ItemResult(state.name, True, True, "updated; reinstalls at restart",
                          manual_fix=fix,
                          pending_install=PendingInstall(state.root, tuple(state.dists)))
    ok, output = _run(pip_install_command(python, str(state.root)), run, log)
    verified, verify_output = _verify(python, state.dists, run, log)
    if not ok or not verified:
        message = "reinstall failed" if not ok else "reinstalled package does not import"
        return ItemResult(state.name, False, True, message, _join(output, verify_output),
                          install_failed=True, manual_fix=fix)
    return ItemResult(state.name, True, changed, "updated", output)


def apply_updates(
    repos: list[RepoState],
    pypi: list[PypiState],
    python: str = sys.executable,
    run: Run = subprocess.run,
    log: Log = lambda s: None,
    defer_install: bool = True,
) -> list[ItemResult]:
    """Update the given repos and packages; one result per item, in input order.

    Blocking (run it off the GUI thread). Per-item failures are reported in the
    results, never raised. Every command and its output go to ``log`` and to the
    app logger.

    A checkout whose ``pyproject.toml`` changed needs ``pip install -e``. With
    ``defer_install`` (the default), pip does not run here. The result carries
    a :class:`PendingInstall`, and :func:`lightfall.updates.deferred.schedule`
    runs it after Lightfall exits. Without it, pip runs now and an import in a
    fresh interpreter checks the result. Index (PyPI) upgrades always run now
    and get the same import check.

    Raises:
        UpdateBlocked: if a scan is running.
    """
    if scan_running():
        raise UpdateBlocked("a scan is running")
    log = _tee(log)
    results = [_apply_repo(state, python, run, log, defer_install) for state in repos]
    if pypi:
        names = [p.name for p in pypi]
        cmd = [python, "-m", "pip", "install", "--upgrade", *names]
        ok, output = _run(cmd, run, log)
        for name in names:
            verified, verify_output = _verify(python, [name], run, log)
            if ok and verified:
                results.append(ItemResult(name, True, True, "updated", output))
            else:
                message = "pip upgrade failed" if not ok else "upgraded package does not import"
                results.append(ItemResult(name, False, ok, message, _join(output, verify_output),
                                          install_failed=True,
                                          manual_fix=subprocess.list2cmdline(cmd)))
    for result in results:
        if not result.ok:
            logger.warning("[update] {} failed: {}\n{}", result.name, result.message,
                           result.output.rstrip())
    return results
