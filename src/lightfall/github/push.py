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
from dataclasses import dataclass
from pathlib import Path

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

_WEAKENING_SUFFIXES = (".sslcainfo", ".sslcapath", ".curloptresolve", ".extraheader",
                       ".sslbackend")

_SYSTEM_OK_SUFFIXES = (".sslbackend", ".sslcainfo", ".sslcapath")

_GITHUB_URL = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

_DENIED = (
    "GitHub refused the push: the Lightfall app may not be installed on {slug}, or your "
    "account lacks write access. Try signing in with a fine-grained token instead."
)
_REJECTED = "The remote branch has commits you don't have; pull first."


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
    stderr = error.stderr if error.stderr is not None else str(error)
    low = stderr.lower()
    if any(s.lower() in low for s in ("403", "Permission to", "denied", "Repository not found")):
        slug = target.url.removeprefix("https://github.com/").removesuffix(".git")
        return _DENIED.format(slug=slug)
    if "non-fast-forward" in low or "rejected" in low:
        return _REJECTED
    return stderr


def _unsafe_config(root: Path, target: PushTarget) -> str | None:
    """Return the first git config key that could redirect or weaken the push."""
    try:
        listing = run_git(root, "config", "--show-scope", "--get-regexp",
                         r"^(url|http)\.")
    except GitError:
        return None  # no matching keys
    protected = (target.url, "git@github.com:", "ssh://git@github.com/")
    for line in listing.splitlines():
        scope, _, entry = line.partition("	")
        key, _, value = entry.partition(" ")
        low = key.lower()
        if low.startswith("url.") and low.endswith((".insteadof", ".pushinsteadof")):
            if value and any(p.startswith(value) for p in protected):
                return key
        elif low.startswith("http."):
            if low.endswith(".sslverify") and value.strip().lower() in (
                    "false", "no", "off", "0"):
                return key
            if low.endswith(_WEAKENING_SUFFIXES):
                # Git for Windows ships sslBackend/sslCAInfo in its system config;
                # that scope is admin-installed, not repo- or user-controlled.
                if scope == "system" and low.endswith(_SYSTEM_OK_SUFFIXES):
                    continue
                return key
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
    target: PushTarget, cred: GitHubCredential, *, url: str | None = None
) -> PushResult:
    """Push ``HEAD`` to ``refs/heads/<branch>`` on the remote; never forced."""
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
            before = run_git(root, "rev-parse", "--verify", "-q",
                             f"refs/remotes/{target.remote}/{branch}")
        except GitError:
            before = None
        env: dict[str, str | None] = dict.fromkeys(_TLS_ENV)
        env[TOKEN_ENV] = cred.token
        with tempfile.TemporaryDirectory(prefix="lf-nohooks-") as no_hooks:
            out = run_git(
                root, *STALL_LIMITS, "-c", "credential.helper=", "-c", HELPER,
                "-c", f"core.hooksPath={no_hooks}",
                "-c", "http.sslVerify=true", "-c", "http.https://github.com/.sslVerify=true",
                "push", "--porcelain", "--no-verify", url or target.url,
                f"HEAD:refs/heads/{branch}",
                env=env, timeout=120,
            )
    except GitError as e:
        return PushResult(False, scrub(_describe_failure(e, target), cred.token),
                          target, None, None)
    old, new = _remote_heads(root, target, out, before, head)
    message = f"Pushed {branch} to {target.url}"
    try:
        if not target.has_upstream:
            run_git(root, "config", f"branch.{branch}.remote", target.remote)
            run_git(root, "config", f"branch.{branch}.merge", f"refs/heads/{branch}")
        run_git(root, "update-ref", f"refs/remotes/{target.remote}/{branch}", "HEAD")
    except GitError as e:
        message += f" (pushed, but could not update local tracking: {e.stderr or e})"
    return PushResult(True, scrub(message, cred.token), target, old, new)
