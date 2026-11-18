"""Push a repository's current branch to GitHub with the session credential.

The token reaches git only through the environment (read by a throwaway
credential helper); it is never placed in argv, a URL, or git config. The
helper answers only for https://github.com, hooks are disabled for the push,
TLS verification is pinned on, and the push is refused when git config would
redirect it (``insteadOf``) or weaken TLS.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lightfall.github.session import GitHubCredential, scrub
from lightfall.updates.git import STALL_LIMITS, GitError, run_git

TOKEN_ENV = "LIGHTFALL_GITHUB_TOKEN"
# Answers only ``get`` for https://github.com; any other host/operation prints nothing.
HELPER = (
    'credential.helper=!f() { [ "$1" = get ] || exit 0; p=; h=; '
    'while IFS= read -r l; do case "$l" in protocol=https) p=1;; host=github.com) h=1;; '
    'esac; done; [ -n "$p" ] && [ -n "$h" ] || exit 0; '
    'echo username=x-access-token; echo "password=$LIGHTFALL_GITHUB_TOKEN"; }; f'
)

#: Environment variables that could weaken TLS for the push; removed from its env.
_TLS_ENV = ("GIT_SSL_NO_VERIFY", "GIT_SSL_CAINFO", "GIT_SSL_CAPATH", "GIT_SSL_CERT",
            "GIT_SSL_KEY")
#: Tracing variables that could write the credential exchange to stderr or a file.
_TRACE_ENV = ("GIT_TRACE", "GIT_TRACE_CURL", "GIT_TRACE_PACKET", "GIT_CURL_VERBOSE",
              "GIT_TRACE_REDACT")

_GITHUB_URL = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

_DENIED = (
    "GitHub refused the push: the Lightfall app may not be installed on {slug}, or your "
    "account lacks write access. Try signing in with a fine-grained token instead."
)
_REJECTED = "The remote branch has commits you don't have; pull first."
_CHANGED = "Not pushed: the repository changed after you approved; try again."
GATE_CLOSED = ("Not pushed: pushing requires being logged in, online, with the "
               "system-admin permission.")
SIGN_IN_CHANGED = "Not pushed: GitHub sign-in changed after you approved."


def github_https_url(url: str) -> str | None:
    """Normalize a github.com remote URL to its https form, or None if it isn't one."""
    m = _GITHUB_URL.match(url.strip())
    if not m or {m["owner"], m["repo"]} & {".", ".."}:
        return None
    return f"https://github.com/{m['owner']}/{m['repo']}.git"


@dataclass(frozen=True)
class PushTarget:
    root: Path
    branch: str
    remote: str
    url: str  # https form
    has_upstream: bool
    ahead: int


@dataclass(frozen=True)
class PushResult:
    ok: bool
    message: str
    target: PushTarget | None
    old_head: str | None  # remote branch head before the push (None: new branch)
    new_head: str | None  # remote branch head after the push


def _config(root: Path, key: str) -> str | None:
    try:
        return run_git(root, "config", "--get", key) or None
    except GitError:
        return None


def push_target(root: Path) -> PushTarget | str:
    """Describe what pushing ``root``'s current branch would do, or why it can't."""
    try:
        branch = run_git(root, "symbolic-ref", "--short", "-q", "HEAD")
    except GitError:
        return "detached HEAD"
    remote = (
        _config(root, f"branch.{branch}.pushRemote")
        or _config(root, "remote.pushDefault")
        or _config(root, f"branch.{branch}.remote")
        or "origin"
    )
    raw = _config(root, f"remote.{remote}.pushurl") or _config(root, f"remote.{remote}.url")
    url = github_https_url(raw) if raw else None
    if url is None:
        return "no GitHub remote"
    try:
        run_git(root, "rev-parse", "--verify", "-q", "HEAD")
    except GitError:
        return "nothing to push"
    try:
        ahead = int(run_git(root, "rev-list", "--count", "@{u}..HEAD"))
        has_upstream = True
    except (GitError, ValueError):
        has_upstream = False
        try:
            ahead = int(run_git(root, "rev-list", "--count", "HEAD", "--not",
                                f"--remotes={remote}"))
        except (GitError, ValueError):
            ahead = 1
    if has_upstream and ahead == 0:
        return "nothing to push"
    return PushTarget(root, branch, remote, url, has_upstream, ahead)


def _describe_failure(error: GitError, target: PushTarget) -> str:
    # Classify on git's own stderr only: the command prefix can contain the URL.
    if error.stderr is None:  # timeout / git missing: never echo the command line
        return "git push timed out" if "timed out" in str(error) else "git push failed"
    stderr = error.stderr
    low = stderr.lower()
    if any(s.lower() in low for s in ("403", "Permission to", "denied", "Repository not found")):
        slug = target.url.removeprefix("https://github.com/").removesuffix(".git")
        return _DENIED.format(slug=slug)
    if "non-fast-forward" in low or "rejected" in low:
        return _REJECTED
    return stderr


def _cfg(root: Path, *args: str) -> tuple[int | None, str]:
    """Run ``git config``; return (exit code, stdout). Code None means git itself failed."""
    try:
        return 0, run_git(root, "config", *args)
    except GitError as e:
        return e.returncode, ""


#: Keys whose effective value must come from system scope (Git for Windows sets them there).
_SYSTEM_ONLY_KEYS = ("http.sslcainfo", "http.sslcapath", "http.sslbackend")
#: Keys that are refused at any scope.
_NEVER_KEYS = ("http.sslcert", "http.sslkey", "http.curloptresolve", "http.extraheader")


def _unsafe_config(root: Path, target: PushTarget) -> str | None:
    """Return a git config key that could redirect or weaken the push, or None.

    Asks git itself to resolve the effective values (URL-scoped keys, includes,
    every spelling of false) and fails closed on anything unexpected.
    """
    url = target.url
    # TLS verification: must resolve to true (unset defaults to true).
    code, out = _cfg(root, "--type=bool", "--get-urlmatch", "http.sslverify", url)
    if not (code == 1 or (code == 0 and out == "true")):
        return "http.sslVerify"
    # Other TLS / request-shaping keys.
    for key in _SYSTEM_ONLY_KEYS + _NEVER_KEYS:
        code, out = _cfg(root, "--show-scope", "--null", "--get-urlmatch", key, url)
        if code == 1:
            continue
        if code != 0:
            return key
        if key in _SYSTEM_ONLY_KEYS:
            fields = out.split("\0")
            scopes = fields[0::2] if len(fields) > 1 else fields
            if all(sc == "system" for sc in scopes if sc):
                continue
        return key
    # URL rewriting: any insteadOf / pushInsteadOf whose value is a prefix of the target.
    code, out = _cfg(root, "--null", "--get-regexp", r"^url\..*\.(insteadof|pushinsteadof)$")
    if code not in (0, 1):
        return "url.*.insteadOf"
    protected = (url, "git@github.com:", "ssh://git@github.com/")
    for entry in out.split("\0") if code == 0 else ():
        if not entry:
            continue
        key, _, value = entry.partition("\n")
        if any(p.startswith(value) for p in protected):
            return key
    # Belt and braces: git's own rewriting must leave the URL untouched.
    try:
        if run_git(root, "ls-remote", "--get-url", url) != url:
            return "url.*.insteadOf"
    except GitError:
        return "url.*.insteadOf"
    return None


_RANGE = re.compile(r"([0-9a-f]+)\.\.\.?([0-9a-f]+)")


def _remote_heads(root: Path, target: PushTarget, porcelain: str, before: str | None,
                  head: str) -> tuple[str | None, str | None]:
    """Old and new remote head of the pushed branch, from ``push --porcelain`` output."""
    for line in porcelain.splitlines():
        fields = line.split("\t")
        if len(fields) < 3 or not fields[1].endswith(f":refs/heads/{target.branch}"):
            continue
        summary = fields[2]
        if "[new branch]" in summary:
            return None, head
        m = _RANGE.match(summary)
        if m:
            try:
                return run_git(root, "rev-parse", "--verify", "-q", m[1]), head
            except GitError:
                pass
        break
    return before, head


def push_repo(
    target: PushTarget,
    cred: GitHubCredential,
    *,
    url: str | None = None,
    expected_head: str | None = None,
) -> PushResult:
    """Push ``HEAD`` to ``refs/heads/<branch>`` on the remote; never forced.

    The push is refused unless the checkout is still on ``target.branch``. With
    ``expected_head``, it must also be at exactly that commit, and that commit is
    what is pushed.
    """
    root, branch = target.root, target.branch
    try:
        key = _unsafe_config(root, target)
        if key is not None:
            return PushResult(
                False,
                f"Not pushed: git config {key} would redirect or weaken the push; "
                "remove it and try again.",
                target, None, None)
        head = run_git(root, "rev-parse", "HEAD")
        try:
            current = run_git(root, "symbolic-ref", "--short", "-q", "HEAD")
        except GitError:
            current = None
        if current != branch or (expected_head is not None and head != expected_head):
            return PushResult(False, _CHANGED, target, None, None)
        pushed = expected_head or "HEAD"
        try:
            before = run_git(root, "rev-parse", "--verify", "-q",
                             f"refs/remotes/{target.remote}/{branch}")
        except GitError:
            before = None
        env: dict[str, str | None] = dict.fromkeys(_TLS_ENV + _TRACE_ENV)
        env[TOKEN_ENV] = cred.token
        try:
            with tempfile.TemporaryDirectory(prefix="lf-nohooks-") as no_hooks:
                out = run_git(
                    root, *STALL_LIMITS, "-c", "credential.helper=", "-c", HELPER,
                    "-c", f"core.hooksPath={no_hooks}",
                    "-c", "http.sslVerify=true",
                    "-c", "http.https://github.com/.sslVerify=true",
                    "-c", "push.recurseSubmodules=no",
                    "push", "--porcelain", "--no-verify", "--recurse-submodules=no",
                    url or target.url,
                    f"{pushed}:refs/heads/{branch}",
                    env=env, timeout=120,
                )
        finally:
            del env  # the token leaves this frame's locals before anything else runs
    except GitError as e:
        return PushResult(False, scrub(_describe_failure(e, target), cred.token),
                          target, None, None)
    except Exception:  # noqa: BLE001 - frames here hold the token; never re-raise
        return PushResult(False, "git push failed", target, None, None)
    old, new = _remote_heads(root, target, out, before, head)
    message = f"Pushed {branch} to {target.url}"
    try:
        if not target.has_upstream:
            run_git(root, "config", f"branch.{branch}.remote", target.remote)
            run_git(root, "config", f"branch.{branch}.merge", f"refs/heads/{branch}")
        run_git(root, "update-ref", f"refs/remotes/{target.remote}/{branch}", head)
    except GitError as e:
        message += f" (pushed, but could not update local tracking: {e.stderr or e})"
    return PushResult(True, scrub(message, cred.token), target, old, new)


def recheck_and_push(
    approved: PushTarget,
    approved_head: str,
    approved_login: str,
    session: Any,
    gate: Callable[[], bool],
    *,
    target: Callable[[Path], PushTarget | str] = push_target,
    push: Callable[..., PushResult] = push_repo,
) -> PushResult:
    """Push what the user approved, after re-checking that it is still what they saw.

    The approval (a confirm dialog, an agent's Allow card) can stay open for a while.
    Afterwards the gate and the credential (same GitHub login) are read again, the
    target is re-derived and must match the approved branch, remote and URL, and the
    push is pinned to ``approved_head``. Never raises; failures come back scrubbed.
    """
    if not gate():
        return PushResult(False, GATE_CLOSED, approved, None, None)
    cred = session.credential()
    if cred is None or cred.login != approved_login:
        return PushResult(False, SIGN_IN_CHANGED, approved, None, None)
    try:
        again = target(approved.root)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return PushResult(False, scrub(f"Not pushed: could not inspect repo: {exc}",
                                       cred.token), approved, None, None)
    if isinstance(again, str) or (again.branch, again.url, again.remote) != (
            approved.branch, approved.url, approved.remote):
        return PushResult(False, _CHANGED, approved, None, None)
    try:
        # Never pass url=: that override skips the redirect/TLS safety checks.
        return push(again, cred, expected_head=approved_head)
    except Exception as exc:  # noqa: BLE001 - no traceback: push frames hold the token
        return PushResult(False, scrub(f"git push failed: {type(exc).__name__}: {exc}",
                                       cred.token), again, None, None)
