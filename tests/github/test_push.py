"""Tests for pushing the current branch to GitHub with a session credential."""

from __future__ import annotations

import subprocess

import pytest

from lightfall.github import push as push_mod
from lightfall.github.push import (
    HELPER,
    TOKEN_ENV,
    PushTarget,
    github_https_url,
    push_repo,
    push_target,
)
from lightfall.github.session import GitHubCredential
from lightfall.updates.git import GitError, git_env
from tests.updates.conftest import commit, git

TOKEN = "github_pat_SECRETSECRET"
GH_URL = "https://github.com/o/r.git"


def make_cred(token: str = TOKEN) -> GitHubCredential:
    return GitHubCredential(token=token, kind="pat", login="octo", expires_at=None,
                            lightfall_user="u")


@pytest.mark.parametrize("url", [
    "https://github.com/o/r", "https://github.com/o/r.git",
    "git@github.com:o/r", "git@github.com:o/r.git",
    "ssh://git@github.com/o/r", "ssh://git@github.com/o/r.git",
])
def test_github_https_url(url):
    assert github_https_url(url) == GH_URL


@pytest.mark.parametrize("url", ["https://git.als.lbl.gov/ncs/x.git", "/local/path"])
def test_github_https_url_rejects_others(url):
    assert github_https_url(url) is None


def test_push_target_reasons(clone):
    assert push_target(clone) == "no GitHub remote"
    git(clone, "config", "remote.origin.url", GH_URL)
    assert push_target(clone) == "nothing to push"
    git(clone, "checkout", "--detach")
    assert push_target(clone) == "detached HEAD"


def test_push_new_branch_sets_upstream(clone, git_remote):
    git(clone, "config", "remote.origin.url", GH_URL)
    git(clone, "checkout", "-b", "feat")
    head = commit(clone, "a.txt", "a\n")
    target = push_target(clone)
    assert isinstance(target, PushTarget)
    assert target.url == GH_URL and not target.has_upstream
    result = push_repo(target, make_cred(), url=str(git_remote))
    assert result.ok, result.message
    assert git(git_remote, "rev-parse", "refs/heads/feat") == head
    assert git(clone, "config", "branch.feat.remote") == "origin"
    assert git(clone, "config", "branch.feat.merge") == "refs/heads/feat"
    assert git(clone, "config", "remote.origin.url") == GH_URL
    assert git(clone, "rev-parse", "refs/remotes/origin/feat") == head


def test_push_never_forces(clone, git_remote, tmp_path):
    git(clone, "config", "remote.origin.url", GH_URL)
    other = tmp_path / "other"
    git(tmp_path, "clone", str(git_remote), str(other))
    commit(other, "x.txt", "x\n")
    git(other, "push", "origin", "master")
    remote_before = git(git_remote, "rev-parse", "refs/heads/master")
    commit(clone, "b.txt", "b\n")
    target = push_target(clone)
    assert isinstance(target, PushTarget)
    result = push_repo(target, make_cred(), url=str(git_remote))
    assert not result.ok
    assert "pull first" in result.message
    assert git(git_remote, "rev-parse", "refs/heads/master") == remote_before


def _target(tmp_path) -> PushTarget:
    return PushTarget(tmp_path, "feat", "origin", GH_URL, False, 1)


def test_token_not_in_argv(monkeypatch, tmp_path):
    calls = []

    def rec(root, *args, **kw):
        calls.append((args, kw))
        return ""

    monkeypatch.setattr(push_mod, "run_git", rec)
    push_repo(_target(tmp_path), make_cred())
    assert calls
    for args, _kw in calls:
        assert all(TOKEN not in a for a in args)
    push_call = next(c for c in calls if "push" in c[0])
    assert push_call[1]["env"][TOKEN_ENV] == TOKEN
    argv = push_call[0]
    assert all("--force" not in a and "-u" != a for a in argv)
    assert "--no-verify" in argv
    assert "HEAD:refs/heads/feat" in argv
    assert argv.index("credential.helper=") < argv.index(HELPER)


def test_push_error_is_scrubbed(monkeypatch, tmp_path):
    def boom(root, *args, **kw):
        raise GitError(f"fatal: https://x-access-token:{TOKEN}@github.com/o/r.git denied")

    monkeypatch.setattr(push_mod, "run_git", boom)
    result = push_repo(_target(tmp_path), make_cred())
    assert not result.ok and TOKEN not in result.message


def test_permission_denied_message_suggests_pat(monkeypatch, tmp_path):
    def boom(root, *args, **kw):
        raise GitError("remote: Permission to o/r.git denied to octo. 403")

    monkeypatch.setattr(push_mod, "run_git", boom)
    result = push_repo(_target(tmp_path), make_cred())
    assert "fine-grained token" in result.message


def test_helper_supplies_token_from_env():
    proc = subprocess.run(
        ["git", "-c", "credential.helper=", "-c", HELPER, "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True,
        env={**git_env(), TOKEN_ENV: "github_pat_TEST"}, check=True,
    )
    assert "password=github_pat_TEST" in proc.stdout
    assert "username=x-access-token" in proc.stdout


def test_github_url_rejects_dot_segments():
    assert github_https_url("https://github.com/./r") is None
    assert github_https_url("https://github.com/o/..") is None
