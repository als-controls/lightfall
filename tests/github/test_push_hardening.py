"""Hardening tests: the credential never leaves github.com or reaches repo-controlled code."""

from __future__ import annotations

import subprocess

from lightfall.github import push as push_mod
from lightfall.github.push import HELPER, TOKEN_ENV, PushTarget, push_repo, push_target
from lightfall.updates.git import git_env
from tests.github.test_push import GH_URL, make_cred
from tests.updates.conftest import commit, git


def _recording(monkeypatch):
    calls = []
    real = push_mod.run_git

    def rec(root, *args, **kw):
        calls.append(args)
        return real(root, *args, **kw)

    monkeypatch.setattr(push_mod, "run_git", rec)
    return calls


def _github_clone(clone):
    git(clone, "config", "remote.origin.url", GH_URL)
    git(clone, "checkout", "-b", "feat")
    commit(clone, "a.txt", "a\n")
    target = push_target(clone)
    assert isinstance(target, PushTarget)
    return target


def test_helper_ignores_other_hosts():
    def run(op, stdin):
        return subprocess.run(
            ["git", "-c", "credential.helper=", "-c", HELPER, "credential", op],
            input=stdin, capture_output=True, text=True, check=True,
            env={**git_env(), TOKEN_ENV: "github_pat_TEST"},
        ).stdout

    # git's prompt fallback (GIT_ASKPASS=echo) prints "password=Password for ...";
    # what matters is that the token and the helper's username never appear.
    for stdin in ("protocol=https\nhost=evil.example\n\n", "protocol=http\nhost=github.com\n\n"):
        out = run("fill", stdin)
        assert "github_pat_TEST" not in out and "x-access-token" not in out
    full = "protocol=https\nhost=github.com\nusername=a\npassword=b\n\n"
    assert run("approve", full) == ""
    assert run("reject", full) == ""


def test_insteadof_redirect_refused(clone, git_remote, monkeypatch):
    target = _github_clone(clone)
    git(clone, "config", f"url.{git_remote.as_posix()}.insteadOf", "https://github.com/")
    calls = _recording(monkeypatch)
    result = push_repo(target, make_cred())
    assert not result.ok and "Not pushed" in result.message
    assert not any("push" in a for a in calls)


def test_ssl_weakening_refused(clone, monkeypatch):
    target = _github_clone(clone)
    git(clone, "config", "http.https://github.com/.sslVerify", "false")
    calls = _recording(monkeypatch)
    result = push_repo(target, make_cred())
    assert not result.ok and "Not pushed" in result.message
    assert not any("push" in a for a in calls)


def test_push_disables_hooks(clone, git_remote, tmp_path):
    target = _github_clone(clone)
    leak = tmp_path / "leak.txt"
    for name in ("pre-push", "reference-transaction"):
        hook = clone / ".git" / "hooks" / name
        hook.write_text(f'#!/bin/sh\necho "$LIGHTFALL_GITHUB_TOKEN" >> "{leak.as_posix()}"\n',
                        newline="\n")
        hook.chmod(0o755)
    result = push_repo(target, make_cred(), url=str(git_remote))
    assert result.ok, result.message
    assert not leak.exists() or leak.read_text().strip() == ""


def test_new_branch_remote_heads(clone, git_remote):
    target = _github_clone(clone)
    head = git(clone, "rev-parse", "HEAD")
    result = push_repo(target, make_cred(), url=str(git_remote))
    assert result.ok, result.message
    assert result.old_head is None and result.new_head == head


def test_fast_forward_remote_heads(clone, git_remote):
    git(clone, "config", "remote.origin.url", GH_URL)
    old = git(git_remote, "rev-parse", "refs/heads/master")
    new = commit(clone, "b.txt", "b\n")
    target = push_target(clone)
    assert isinstance(target, PushTarget)
    result = push_repo(target, make_cred(), url=str(git_remote))
    assert result.ok, result.message
    assert result.old_head == old and result.new_head == new
