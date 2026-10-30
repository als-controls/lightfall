"""Tests for reading git repo state, fetching quietly, and fast-forward pulls."""

from __future__ import annotations

import subprocess

import pytest

from lightfall.updates import git as git_mod
from lightfall.updates.git import (
    GitError,
    git_env,
    pull_ff,
    pyproject_changed,
    read_repo_state,
    run_git,
)

from .conftest import commit, git, make_clone


def push_upstream(git_remote, tmp_path, path="file.txt", text="new\n"):
    """Advance the remote by one commit from a second clone."""
    other = make_clone(git_remote, tmp_path / "other")
    commit(other, path, text)
    git(other, "push", "origin", "master")


def state(repo, fetch=True):
    return read_repo_state(repo, ("lightfall",), fetch=fetch)


def test_git_env_disables_prompts():
    env = git_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"
    assert env["GIT_ASKPASS"]


def test_clean_current_repo(clone):
    s = state(clone)
    assert s.on_default and not s.dirty
    assert s.default_branch == "master" and s.remote == "origin"
    assert s.behind == 0 and s.ahead == 0
    assert not s.has_update and not s.noteworthy
    assert s.blockers == []


def test_behind_has_update(clone, git_remote, tmp_path):
    push_upstream(git_remote, tmp_path)
    s = state(clone)
    assert s.behind == 1
    assert s.has_update
    assert s.remote_head is not None and s.remote_head != s.head


def test_untracked_is_not_dirty(clone):
    (clone / "scratch.txt").write_text("x", encoding="utf-8")
    assert state(clone).dirty is False


def test_tracked_edit_is_dirty_and_blocks(clone):
    (clone / "README.md").write_text("edited\n", encoding="utf-8")
    s = state(clone)
    assert s.dirty
    assert "uncommitted changes" in s.blockers
    assert s.noteworthy


def test_feature_branch_never_has_update(clone, git_remote, tmp_path):
    git(clone, "checkout", "-b", "feat/x")
    push_upstream(git_remote, tmp_path)
    s = state(clone)
    assert s.branch == "feat/x"
    assert not s.has_update
    assert "on branch feat/x, not master" in s.blockers
    assert s.noteworthy


def test_detached_head(clone):
    git(clone, "checkout", "--detach")
    s = state(clone)
    assert s.branch is None
    assert "detached HEAD" in s.blockers
    assert s.noteworthy


def test_ahead_blocks_and_is_noteworthy(clone):
    commit(clone, "local.txt", "local\n")
    s = state(clone)
    assert s.ahead == 1
    assert "1 unpushed commit(s)" in s.blockers
    assert s.noteworthy


def test_unreachable_remote_is_quiet(clone, tmp_path):
    git(clone, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    s = state(clone)
    assert s.reachable is False
    assert s.ahead is None and s.behind is None
    assert not s.has_update


def test_local_refresh_uses_existing_tracking_ref(clone):
    s = state(clone, fetch=False)
    assert s.behind == 0 and s.ahead == 0


def test_missing_git_is_quiet(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(git_mod, "GIT_MISSING", False)
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(GitError):
        run_git(tmp_path, "status")
    assert git_mod.GIT_MISSING is True


def test_pull_ff_and_pyproject_changed(clone, git_remote, tmp_path):
    push_upstream(git_remote, tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    old, new = pull_ff(state(clone))
    assert old != new
    assert git(clone, "rev-parse", "HEAD") == new
    assert pyproject_changed(clone, old, new) is True


def test_pyproject_unchanged_for_other_files(clone, git_remote, tmp_path):
    push_upstream(git_remote, tmp_path, "src.py", "x = 1\n")
    old, new = pull_ff(state(clone))
    assert pyproject_changed(clone, old, new) is False
