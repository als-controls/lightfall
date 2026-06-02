"""Auto-commit wrapper around `git` CLI for the user lightfall directory.

Backs the §4 paper claim: every agent-produced change in ``~/lightfall/`` becomes
a commit. Used by both :class:`UserPluginService` and :class:`UserPlanService`
so that plugins, plans, and any other staff-owned files in ``~/lightfall/`` share
a single linear history.

The tracker is forgiving: missing ``git`` executable, missing identity,
or paths outside the repo root degrade to a logged warning rather than
raising — plugin creation must not fail because git is misconfigured.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import ClassVar

from loguru import logger


class GitTracker:
    """Singleton wrapping the ``~/lightfall/`` git repo."""

    _instance: ClassVar[GitTracker | None] = None
    _lock = threading.RLock()

    DEFAULT_USER_EMAIL = "lightfall-agent@als.lbl.gov"
    DEFAULT_USER_NAME = "Lightfall Agent"

    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = (
            repo_root if repo_root is not None else Path.home() / "lightfall"
        )

    @classmethod
    def get_instance(cls) -> GitTracker:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # repo bootstrap

    def ensure_repo(self) -> bool:
        """Initialize ``repo_root`` as a git repo if not already.

        Idempotent. Sets a local ``user.email``/``user.name`` so commits
        work on machines without a global git identity.
        """
        try:
            self.repo_root.mkdir(parents=True, exist_ok=True)
            if (self.repo_root / ".git").exists():
                self._ensure_local_identity()
                return True
            subprocess.run(
                ["git", "init"], cwd=self.repo_root, check=True,
                capture_output=True,
            )
            self._ensure_local_identity()
            logger.info("Initialized git repo at {}", self.repo_root)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            logger.warning("Could not init git repo at {}: {}", self.repo_root, e)
            return False

    def _ensure_local_identity(self) -> None:
        """Set local user.email/name to the Lightfall Agent identity if not already configured locally.

        Intentionally ignores any global git identity. The §4 paper claim is
        that agent-produced changes in ``~/lightfall/`` are attributable to
        "Lightfall Agent" -- allowing the developer's global identity to leak
        through would muddy that forensic record. A user who deliberately
        sets a different *local* identity via ``git config --local`` is
        still respected.

        Each field (user.email and user.name) is checked and set
        independently. If a staff member's existing repo became
        ``~/lightfall/`` and they had only ``user.email`` set locally, we set
        only the missing ``user.name`` rather than clobbering both.
        """
        existing_email = self._git_config_get_local("user.email")
        existing_name = self._git_config_get_local("user.name")
        if existing_email and existing_name:
            return
        try:
            if not existing_email:
                subprocess.run(
                    ["git", "config", "--local", "user.email", self.DEFAULT_USER_EMAIL],
                    cwd=self.repo_root, check=True, capture_output=True,
                )
            if not existing_name:
                subprocess.run(
                    ["git", "config", "--local", "user.name", self.DEFAULT_USER_NAME],
                    cwd=self.repo_root, check=True, capture_output=True,
                )
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            logger.warning("Failed to set local git identity: {}", e)

    def _git_config_get_local(self, key: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "config", "--local", "--get", key],
                cwd=self.repo_root, capture_output=True, text=True,
            )
            if out.returncode == 0:
                return out.stdout.strip() or None
            return None
        except FileNotFoundError:
            return None

    # ------------------------------------------------------------------
    # commits

    def commit(self, paths: list[Path], message: str) -> bool:
        """Stage ``paths`` and commit with ``message``.

        Returns True on commit, False on no-op (no diff) or on error.
        Errors are logged, not raised — never block the caller.
        """
        rel_paths = self._validate_paths(paths)
        if not rel_paths:
            return False
        if not self.ensure_repo():
            return False
        try:
            subprocess.run(
                ["git", "add", "--", *rel_paths],
                cwd=self.repo_root, check=True, capture_output=True,
            )
            # No-op detection: --quiet returns 0 if no staged changes
            diff = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=self.repo_root,
            )
            if diff.returncode == 0:
                return False
            subprocess.run(
                ["git", "commit", "-m", message, "--", *rel_paths],
                cwd=self.repo_root, check=True, capture_output=True,
            )
            logger.debug("git commit {} -m {!r}", rel_paths, message)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            logger.warning("git commit failed for {}: {}", rel_paths, e)
            return False

    def commit_removal(self, paths: list[Path], message: str) -> bool:
        """Stage deletions of ``paths`` and commit.

        ``paths`` should already be deleted from the working tree.
        """
        rel_paths = self._validate_paths(paths)
        if not rel_paths:
            return False
        if not self.ensure_repo():
            return False
        try:
            subprocess.run(
                ["git", "add", "--all", "--", *rel_paths],
                cwd=self.repo_root, check=True, capture_output=True,
            )
            diff = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=self.repo_root,
            )
            if diff.returncode == 0:
                return False
            subprocess.run(
                ["git", "commit", "-m", message, "--", *rel_paths],
                cwd=self.repo_root, check=True, capture_output=True,
            )
            logger.debug("git commit (removal) {} -m {!r}", rel_paths, message)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            logger.warning("git commit (removal) failed for {}: {}", rel_paths, e)
            return False

    def _validate_paths(self, paths: list[Path]) -> list[str]:
        """Return repo-relative path strings, dropping any outside the root."""
        out: list[str] = []
        root = self.repo_root.resolve()
        for p in paths:
            try:
                rel = p.resolve().relative_to(root)
            except ValueError:
                logger.warning("Path {} is outside repo {}; skipping", p, root)
                continue
            out.append(rel.as_posix())
        return out
