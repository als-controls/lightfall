"""Push a repository's current branch to GitHub with the session credential.

The token reaches git only through the environment (read by a throwaway
credential helper); it is never placed in argv, a URL, or git config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lightfall.github.session import GitHubCredential, scrub
from lightfall.updates.git import STALL_LIMITS, GitError, run_git

TOKEN_ENV = "LIGHTFALL_GITHUB_TOKEN"
HELPER = (
    'credential.helper=!f() { echo username=x-access-token; '
    'echo "password=$LIGHTFALL_GITHUB_TOKEN"; }; f'
)

_GITHUB_URL = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"
)

_DENIED = "GitHub refused the push: the Lightfall app may not be installed on {slug}, or your account lacks write access. Try signing in with a fine-grained token instead."
_REJECTED = "The remote branch has commits you don't have; pull first."


def github_https_url(url: str) -> str | None:
    """Normalize a github.com remote URL to its https form, or None if it isn't one."""
    m = _GITHUB_URL.match(url.strip())
    if not m:
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
    old_head: str | None
    new_head: str | None


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
        ahead, has_upstream = 1, False
    if has_upstream and ahead == 0:
        return "nothing to push"
    return PushTarget(root, branch, remote, url, has_upstream, ahead)


def _describe_failure(error: str, target: PushTarget) -> str:
    low = error.lower()
    if any(s.lower() in low for s in ("403", "Permission to", "denied", "Repository not found")):
        slug = target.url.removeprefix("https://github.com/").removesuffix(".git")
        return _DENIED.format(slug=slug)
    if "non-fast-forward" in low or "rejected" in low:
        return _REJECTED
    return error


def push_repo(
    target: PushTarget, cred: GitHubCredential, *, url: str | None = None
) -> PushResult:
    """Push ``HEAD`` to ``refs/heads/<branch>`` on the remote; never forced."""
    root, branch = target.root, target.branch
    old_head = None
    try:
        old_head = run_git(root, "rev-parse", "HEAD")
        run_git(
            root, *STALL_LIMITS, "-c", "credential.helper=", "-c", HELPER,
            "push", "--porcelain", url or target.url, f"HEAD:refs/heads/{branch}",
            env={TOKEN_ENV: cred.token}, timeout=120,
        )
        if not target.has_upstream:
            run_git(root, "config", f"branch.{branch}.remote", target.remote)
            run_git(root, "config", f"branch.{branch}.merge", f"refs/heads/{branch}")
        run_git(root, "update-ref", f"refs/remotes/{target.remote}/{branch}", "HEAD")
    except GitError as e:
        message = scrub(_describe_failure(scrub(str(e), cred.token), target), cred.token)
        return PushResult(False, message, target, old_head, None)
    return PushResult(True, scrub(f"Pushed {branch} to {target.url}", cred.token),
                      target, old_head, old_head)
