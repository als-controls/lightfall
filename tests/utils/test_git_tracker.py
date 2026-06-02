"""Tests for GitTracker — auto-commit wrapper around git CLI."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lightfall.utils.git_tracker import GitTracker


@pytest.fixture(autouse=True)
def reset_singleton():
    GitTracker.reset_instance()
    yield
    GitTracker.reset_instance()


@pytest.fixture
def repo_root(tmp_path):
    """A clean ~/lightfall/ stand-in."""
    root = tmp_path / "lightfall"
    root.mkdir()
    return root


@pytest.fixture
def tracker(repo_root):
    t = GitTracker(repo_root=repo_root)
    return t


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def test_ensure_repo_creates_git_dir(tracker, repo_root):
    assert not (repo_root / ".git").exists()
    assert tracker.ensure_repo() is True
    assert (repo_root / ".git").is_dir()


def test_ensure_repo_idempotent(tracker, repo_root):
    tracker.ensure_repo()
    head_before = _git(repo_root, "rev-parse", "--git-dir")
    tracker.ensure_repo()
    head_after = _git(repo_root, "rev-parse", "--git-dir")
    assert head_before == head_after


def test_ensure_repo_sets_local_identity(tracker, repo_root):
    tracker.ensure_repo()
    email = _git(repo_root, "config", "--local", "user.email")
    name = _git(repo_root, "config", "--local", "user.name")
    assert email == "lightfall-agent@als.lbl.gov"
    assert name == "LUCID Agent"


def test_ensure_repo_preserves_partial_existing_local_identity(repo_root):
    """If user has set only user.email locally, do not clobber it; set only user.name."""
    # Pre-init the repo and set only user.email
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "--local", "user.email", "staff@als.lbl.gov"],
        cwd=repo_root, check=True,
    )
    GitTracker.reset_instance()
    tracker = GitTracker(repo_root=repo_root)
    tracker.ensure_repo()
    assert _git(repo_root, "config", "--local", "user.email") == "staff@als.lbl.gov"
    # name should now be the default
    assert _git(repo_root, "config", "--local", "user.name") == "LUCID Agent"


def test_commit_creates_commit_with_message(tracker, repo_root):
    plugins_dir = repo_root / "plugins"
    plugins_dir.mkdir()
    plugin_file = plugins_dir / "foo.py"
    plugin_file.write_text("# hello\n", encoding="utf-8")

    assert tracker.commit([plugin_file], "agent-edit: add foo") is True

    log = _git(repo_root, "log", "--format=%s")
    assert log == "agent-edit: add foo"


def test_commit_is_noop_when_no_diff(tracker, repo_root):
    plugins_dir = repo_root / "plugins"
    plugins_dir.mkdir()
    plugin_file = plugins_dir / "foo.py"
    plugin_file.write_text("# hello\n", encoding="utf-8")
    tracker.commit([plugin_file], "first")

    # Second commit with no change to the file
    assert tracker.commit([plugin_file], "second") is False
    log_lines = _git(repo_root, "log", "--format=%s").splitlines()
    assert log_lines == ["first"]


def test_commit_removal_records_deletion(tracker, repo_root):
    plugins_dir = repo_root / "plugins"
    plugins_dir.mkdir()
    plugin_file = plugins_dir / "foo.py"
    plugin_file.write_text("# hello\n", encoding="utf-8")
    tracker.commit([plugin_file], "add foo")

    plugin_file.unlink()
    assert tracker.commit_removal([plugin_file], "remove foo") is True

    # Verify the deletion is in history
    diff = _git(repo_root, "log", "--diff-filter=D", "--name-only", "--format=")
    assert "plugins/foo.py" in diff.splitlines()


def test_commit_swallows_missing_git_executable(tracker, repo_root, monkeypatch):
    """If git isn't on PATH, commit returns False and does not raise."""
    def boom(*args, **kwargs):
        raise FileNotFoundError("git: command not found")
    monkeypatch.setattr("lightfall.utils.git_tracker.subprocess.run", boom)

    plugin_file = repo_root / "foo.py"
    plugin_file.write_text("x", encoding="utf-8")
    assert tracker.commit([plugin_file], "msg") is False


def test_commit_skips_paths_outside_repo(tracker, repo_root, tmp_path, caplog):
    outside = tmp_path / "outside.py"
    outside.write_text("x", encoding="utf-8")
    assert tracker.commit([outside], "msg") is False


def test_singleton_uses_home_lightfall_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    GitTracker.reset_instance()
    instance = GitTracker.get_instance()
    assert instance.repo_root == Path.home() / "lightfall"
