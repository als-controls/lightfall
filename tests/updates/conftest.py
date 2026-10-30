"""Temporary git repos for the update-check tests: a bare remote and clones of it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` with a throwaway identity; return stripped stdout."""
    result = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "init.defaultBranch=master", "-c", "core.autocrlf=false", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def commit(repo: Path, path: str, text: str, message: str = "change") -> str:
    """Write ``text`` to ``repo/path``, commit it, and return the new HEAD."""
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_clone(remote: Path, dest: Path) -> Path:
    git(remote.parent, "clone", str(remote), str(dest))
    return dest


@pytest.fixture
def git_remote(tmp_path: Path) -> Path:
    """A bare repo with one commit on ``master`` (its HEAD names master)."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-b", "master")
    commit(seed, "README.md", "seed\n", "initial")
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "--bare", str(seed), str(remote))
    return remote


@pytest.fixture
def clone(git_remote: Path, tmp_path: Path) -> Path:
    """A working clone of ``git_remote`` (``origin/HEAD`` points at master)."""
    return make_clone(git_remote, tmp_path / "work")
