"""Run git for the update checks without ever prompting for credentials."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from lightfall.utils.logging import logger

#: Set once git turned out not to be on PATH; every source repo is then quiet.
GIT_MISSING = False


class GitError(Exception):
    """A git command failed, timed out, or git is not installed."""


def git_env() -> dict[str, str]:
    """The environment for git: inherited, with every interactive prompt disabled."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_ASKPASS"] = "echo"
    env["SSH_ASKPASS"] = "echo"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    return env


def run_git(root: Path, *args: str, timeout: float = 30.0) -> str:
    """Run ``git <args>`` in ``root`` and return its stripped stdout.

    Raises:
        GitError: on a non-zero exit, a timeout, or a missing git binary.
    """
    global GIT_MISSING
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, env=git_env(), capture_output=True, text=True,
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as e:
        if not GIT_MISSING:
            logger.info("git is not on PATH; source checkouts will not be checked")
        GIT_MISSING = True
        raise GitError("git not found") from e
    except (subprocess.TimeoutExpired, OSError) as e:
        raise GitError(f"git {' '.join(args)}: {e}") from e
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()
