"""Run git for the update checks without ever prompting for credentials."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from lightfall.utils.logging import logger

#: Set once git turned out not to be on PATH; every source repo is then quiet.
GIT_MISSING = False

#: Keeps git and pip from flashing a console window under ``lightfall-gui``.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Abort transfers that stall (curl has no overall timeout of its own).
STALL_LIMITS = ("-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=20")


class GitError(Exception):
    """A git command failed, timed out, or git is not installed."""


def git_env() -> dict[str, str]:
    """The environment for git: inherited, with every interactive prompt disabled."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_ASKPASS"] = "echo"
    env["SSH_ASKPASS"] = "echo"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")
    return env


def run_git(root: Path, *args: str, timeout: float = 30.0) -> str:
    """Run ``git <args>`` in ``root`` and return its stripped stdout.

    Raises:
        GitError: on a non-zero exit, a timeout, or a missing git binary.
    """
    global GIT_MISSING
    command = " ".join(args)
    try:
        proc = subprocess.Popen(
            ["git", *args], cwd=root, env=git_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", creationflags=NO_WINDOW,
            start_new_session=sys.platform != "win32",
        )
    except FileNotFoundError as e:
        if not GIT_MISSING:
            logger.info("git is not on PATH; source checkouts will not be checked")
        GIT_MISSING = True
        raise GitError("git not found") from e
    except OSError as e:
        raise GitError(f"git {command}: {e}") from e
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        # Kill the whole tree: a surviving git-remote-https/ssh child keeps the
        # pipes open and would block communicate() indefinitely.
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise GitError(f"git {command}: timed out after {timeout:g} s") from e
    if proc.returncode != 0:
        raise GitError(f"git {command}: {stderr.strip()}")
    return stdout.strip()


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=NO_WINDOW, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        proc.kill()


@dataclass(frozen=True)
class RepoState:
    """A source checkout's branch, local changes and position against its remote.

    ``ahead`` / ``behind`` are None when the remote could not be reached (or was
    not fetched and has no remote-tracking ref yet).
    """

    root: Path
    dists: tuple[str, ...]
    branch: str | None  # None: detached HEAD
    default_branch: str
    remote: str
    dirty: bool
    head: str
    ahead: int | None
    behind: int | None
    remote_head: str | None

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def on_default(self) -> bool:
        return self.branch == self.default_branch

    @property
    def reachable(self) -> bool:
        return self.behind is not None

    @property
    def has_update(self) -> bool:
        return self.on_default and self.behind is not None and self.behind > 0

    @property
    def blockers(self) -> list[str]:
        """Why this repo can't be fast-forwarded now; empty when it can."""
        reasons = []
        if self.dirty:
            reasons.append("uncommitted changes")
        if self.branch is None:
            reasons.append("detached HEAD")
        elif not self.on_default:
            reasons.append(f"on branch {self.branch}, not {self.default_branch}")
        if self.ahead:
            reasons.append(f"{self.ahead} unpushed commit(s)")
        return reasons

    @property
    def noteworthy(self) -> bool:
        """Worth showing in the git-state indicator."""
        return not self.on_default or self.dirty or (self.ahead or 0) > 0


def _try_git(root: Path, *args: str) -> str | None:
    try:
        return run_git(root, *args)
    except GitError:
        return None


def _default_branch(root: Path, remote: str) -> str:
    ref = _try_git(root, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD")
    if ref and ref.startswith(f"{remote}/"):
        return ref[len(remote) + 1:]
    for candidate in ("master", "main"):
        if _try_git(root, "rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{candidate}"):
            return candidate
    return "master"


def read_repo_state(root: Path, dists: tuple[str, ...], fetch: bool) -> RepoState:
    """Read ``root``'s state; with ``fetch``, refresh its default branch first.

    A failed fetch leaves ahead/behind as None (unreachable) instead of raising.
    Raises GitError only if ``root`` is not a readable git repo at all.
    """
    head = run_git(root, "rev-parse", "--short", "HEAD")
    branch = _try_git(root, "symbolic-ref", "--short", "-q", "HEAD") or None
    remote = (_try_git(root, "config", f"branch.{branch}.remote") if branch else None) or "origin"
    default = _default_branch(root, remote)
    dirty = bool(run_git(root, "status", "--porcelain", "--untracked-files=no"))
    tracking = f"refs/remotes/{remote}/{default}"
    ahead = behind = remote_head = None
    fetched = True
    if fetch:
        try:
            run_git(root, *STALL_LIMITS, "fetch", "--quiet", remote, default)
        except GitError as e:
            logger.debug("Update check: {} unreachable: {}", root.name, e)
            fetched = False
    if fetched and _try_git(root, "rev-parse", "--verify", "-q", tracking):
        counts = _try_git(root, "rev-list", "--left-right", "--count", f"HEAD...{tracking}")
        if counts:
            left, right = counts.split()
            ahead, behind = int(left), int(right)
            remote_head = _try_git(root, "rev-parse", "--short", tracking)
    return RepoState(root, dists, branch, default, remote, dirty, head, ahead, behind,
                     remote_head)


def pull_ff(state: RepoState) -> tuple[str, str]:
    """Fast-forward ``state.root`` to its remote default branch; return (old, new) HEADs."""
    old = run_git(state.root, "rev-parse", "HEAD")
    run_git(state.root, *STALL_LIMITS, "pull", "--ff-only", "--quiet", state.remote,
            state.default_branch, timeout=120.0)
    new = run_git(state.root, "rev-parse", "HEAD")
    return old, new


def pyproject_changed(root: Path, old: str, new: str) -> bool:
    """Whether ``pyproject.toml`` differs between commits ``old`` and ``new``."""
    return bool(run_git(root, "diff", "--name-only", old, new, "--", "pyproject.toml"))
