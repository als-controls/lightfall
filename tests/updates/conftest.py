"""Temporary git repos for the update-check tests: a bare remote and clones of it."""

from __future__ import annotations

import shutil
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


@pytest.fixture(scope="session")
def _template(tmp_path_factory) -> Path:
    """Build the seed remote and a clone once; tests get copies (git is slow on Windows)."""
    base = tmp_path_factory.mktemp("git-template")
    seed = base / "seed"
    seed.mkdir()
    git(seed, "init", "-b", "master")
    commit(seed, "README.md", "seed\n", "initial")
    git(base, "clone", "--bare", str(seed), str(base / "remote.git"))
    make_clone(base / "remote.git", base / "work")
    return base


@pytest.fixture
def git_remote(_template: Path, tmp_path: Path) -> Path:
    """A bare repo with one commit on ``master`` (its HEAD names master)."""
    remote = tmp_path / "remote.git"
    shutil.copytree(_template / "remote.git", remote)
    return remote


@pytest.fixture
def clone(git_remote: Path, _template: Path, tmp_path: Path) -> Path:
    """A working clone of ``git_remote`` (``origin/HEAD`` points at master)."""
    work = tmp_path / "work"
    shutil.copytree(_template / "work", work)
    git(work, "remote", "set-url", "origin", str(git_remote))
    return work
