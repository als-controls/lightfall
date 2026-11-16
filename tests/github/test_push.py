"""Tests for pushing the current branch to GitHub with a session credential."""

from __future__ import annotations

import subprocess

import pytest

from lightfall.github import push as push_mod
from lightfall.github.push import (
    HELPER,
    TOKEN_ENV,
    PushResult,
    PushTarget,
    github_https_url,
    push_repo,
    push_target,
    recheck_and_push,
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


def _git_error(stderr: str | None, returncode: int | None = 1) -> GitError:
    error = GitError(f"git push ...: {stderr}")
    error.stderr, error.returncode = stderr, returncode
    return error


def fake_git(calls, push_error=None):
    """A run_git stand-in: safe answers for the config checks, optional push failure."""

    def fake(root, *args, **kw):
        calls.append((args, kw))
        if "push" in args:
            if push_error is not None:
                raise push_error
            return ""
        if "--get-urlmatch" in args:
            if "http.sslverify" in args:
                return "true"
            raise _git_error("", 1)
        if "--get-regexp" in args:
            raise _git_error("", 1)
        if "ls-remote" in args:
            return GH_URL
        if "symbolic-ref" in args:
            return "feat"
        if "rev-parse" in args and "HEAD" in args:
            return "a" * 40
        if "rev-parse" in args:
            raise _git_error("", 1)
        return ""

    return fake


def test_token_not_in_argv(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(push_mod, "run_git", fake_git(calls))
    result = push_repo(_target(tmp_path), make_cred())
    assert result.ok, result.message
    for args, _kw in calls:
        assert all(TOKEN not in a for a in args)
    push_call = next(c for c in calls if "push" in c[0])
    assert push_call[1]["env"][TOKEN_ENV] == TOKEN
    argv = push_call[0]
    assert all("--force" not in a and "-u" != a for a in argv)
    assert "--no-verify" in argv
    assert "--recurse-submodules=no" in argv and "push.recurseSubmodules=no" in argv
    assert "HEAD:refs/heads/feat" in argv
    assert argv.index("credential.helper=") < argv.index(HELPER)


def test_push_error_is_scrubbed(monkeypatch, tmp_path):
    err = _git_error(f"fatal: https://x-access-token:{TOKEN}@github.com/o/r.git unreachable")
    monkeypatch.setattr(push_mod, "run_git", fake_git([], err))
    result = push_repo(_target(tmp_path), make_cred())
    assert not result.ok and TOKEN not in result.message and "***" in result.message


def test_permission_denied_message_suggests_pat(monkeypatch, tmp_path):
    err = _git_error("remote: Permission to o/r.git denied to octo. 403")
    monkeypatch.setattr(push_mod, "run_git", fake_git([], err))
    result = push_repo(_target(tmp_path), make_cred())
    assert "fine-grained token" in result.message


def test_timeout_message_has_no_command_line(monkeypatch, tmp_path):
    err = GitError(f"git -c {HELPER} push: timed out after 120 s")
    monkeypatch.setattr(push_mod, "run_git", fake_git([], err))
    result = push_repo(_target(tmp_path), make_cred())
    assert result.message == "git push timed out"


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


def _fake_with_branch(calls, branch="feat"):
    base = fake_git(calls)

    def fake(root, *args, **kw):
        if "symbolic-ref" in args:
            calls.append((args, kw))
            return branch
        return base(root, *args, **kw)

    return fake


def test_expected_head_pushes_that_sha(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(push_mod, "run_git", _fake_with_branch(calls))
    result = push_repo(_target(tmp_path), make_cred(), expected_head="a" * 40)
    assert result.ok, result.message
    argv = next(c for c in calls if "push" in c[0])[0]
    assert f"{'a' * 40}:refs/heads/feat" in argv and "HEAD:refs/heads/feat" not in argv


def test_expected_head_mismatch_refused_without_push(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(push_mod, "run_git", _fake_with_branch(calls))
    result = push_repo(_target(tmp_path), make_cred(), expected_head="b" * 40)
    assert not result.ok and "changed after you approved" in result.message
    assert not any("push" in c[0] for c in calls)


def test_expected_head_branch_switch_refused(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(push_mod, "run_git", _fake_with_branch(calls, branch="other"))
    result = push_repo(_target(tmp_path), make_cred(), expected_head="a" * 40)
    assert not result.ok and "changed after you approved" in result.message
    assert not any("push" in c[0] for c in calls)


def test_unexpected_exception_becomes_failure(monkeypatch, tmp_path):
    def boom(root, *args, **kw):
        if "push" in args:
            raise RuntimeError(f"boom {TOKEN}")
        return fake_git([])(root, *args, **kw)

    monkeypatch.setattr(push_mod, "run_git", boom)
    result = push_repo(_target(tmp_path), make_cred())
    assert not result.ok and result.message == "git push failed"


def test_branch_switch_refused_without_expected_head(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(push_mod, "run_git", _fake_with_branch(calls, branch="other"))
    result = push_repo(_target(tmp_path), make_cred())
    assert not result.ok and "changed after you approved" in result.message
    assert not any("push" in c[0] for c in calls)


def test_detached_head_refused_without_expected_head(monkeypatch, tmp_path):
    calls = []
    base = fake_git(calls)

    def fake(root, *args, **kw):
        if "symbolic-ref" in args:
            raise _git_error("", 1)
        return base(root, *args, **kw)

    monkeypatch.setattr(push_mod, "run_git", fake)
    result = push_repo(_target(tmp_path), make_cred())
    assert not result.ok
    assert not any("push" in c[0] for c in calls)


def test_push_env_strips_tracing_and_tls(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(push_mod, "run_git", fake_git(calls))
    assert push_repo(_target(tmp_path), make_cred()).ok
    env = next(c for c in calls if "push" in c[0])[1]["env"]
    for key in ("GIT_TRACE", "GIT_TRACE_CURL", "GIT_TRACE_PACKET", "GIT_CURL_VERBOSE",
                "GIT_TRACE_REDACT", "GIT_SSL_NO_VERIFY", "GIT_SSL_CAINFO"):
        assert key in env and env[key] is None


def test_token_dropped_from_locals_after_push(monkeypatch, tmp_path):
    import sys

    seen = []
    real = push_mod._remote_heads

    def spy(*args):
        seen.append(dict(sys._getframe(1).f_locals))
        return real(*args)

    monkeypatch.setattr(push_mod, "run_git", fake_git([]))
    monkeypatch.setattr(push_mod, "_remote_heads", spy)
    assert push_repo(_target(tmp_path), make_cred()).ok
    assert "env" not in seen[0]
    assert all(TOKEN not in repr(v) for v in seen[0].values())


# --- recheck_and_push ------------------------------------------------------------


class _Session:
    def __init__(self, cred):
        self.cred = cred

    def credential(self):
        return self.cred


def _recheck(tmp_path, *, gate=True, cred=None, target=None, push=None):
    approved = _target(tmp_path)
    calls = []

    def fake_push(t, c, **kw):
        calls.append((t, c, kw))
        return PushResult(True, "pushed", t, None, "a" * 40)

    result = recheck_and_push(
        approved, "a" * 40, "octo", _Session(make_cred() if cred is None else cred),
        lambda: gate, target=target or (lambda root: approved), push=push or fake_push)
    return result, calls


def test_recheck_pushes_approved_head(tmp_path):
    result, calls = _recheck(tmp_path)
    assert result.ok
    assert len(calls) == 1 and calls[0][2] == {"expected_head": "a" * 40}


def test_recheck_gate_closed(tmp_path):
    result, calls = _recheck(tmp_path, gate=False)
    assert not result.ok and "system-admin" in result.message and calls == []


def test_recheck_signed_out_or_other_login(tmp_path):
    other = GitHubCredential(token="github_pat_OTHER", kind="pat", login="eve",
                             expires_at=None, lightfall_user="u")
    for cred in (other,):
        result, calls = _recheck(tmp_path, cred=cred)
        assert not result.ok and "sign-in changed" in result.message and calls == []
    approved = _target(tmp_path)
    result = recheck_and_push(approved, "a" * 40, "octo", _Session(None), lambda: True,
                              target=lambda r: approved, push=lambda *a, **k: 1 / 0)
    assert not result.ok and "sign-in changed" in result.message


def test_recheck_target_changed(tmp_path):
    moved = PushTarget(tmp_path, "other", "origin", GH_URL, True, 1)
    for found in (moved, "detached HEAD",
                  PushTarget(tmp_path, "feat", "origin", "https://github.com/x/y.git",
                             False, 1)):
        result, calls = _recheck(tmp_path, target=lambda root, f=found: f)
        assert not result.ok and "changed after you approved" in result.message
        assert calls == []


def test_recheck_errors_are_scrubbed(tmp_path):
    def boom_target(root):
        raise RuntimeError(f"inspect {TOKEN}")

    def boom_push(t, c, **kw):
        raise RuntimeError(f"push {TOKEN}")

    for kw in ({"target": boom_target}, {"push": boom_push}):
        result, _ = _recheck(tmp_path, **kw)
        assert not result.ok and TOKEN not in result.message and "***" in result.message
