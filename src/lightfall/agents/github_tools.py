"""MCP tools that let an agent push a source checkout to GitHub, with the user's say-so.

The always-on ``github`` server has a read-only repo listing and a push tool. The push
uses the user's session GitHub credential (never visible to the agent) and always asks
the user first in the chat, offering only Allow and Deny. Anything but Allow pushes
nothing. Handlers run on the agent worker's asyncio loop: git goes to a thread
(``asyncio.to_thread``) and the update checker is read on the Qt thread.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lightfall.agents.tooling import tool_id
from lightfall.github.push import push_repo, push_target
from lightfall.github.session import get_github_session, scrub
from lightfall.plugins.agents._mcp_helpers import mcp_error, mcp_result
from lightfall.updates.git import run_git
from lightfall.utils.logging import logger

try:
    from lightfall.claude._internal.threading import run_on_main_thread
except ImportError:  # pragma: no cover - SDK-optional / headless environments
    def run_on_main_thread(func: Callable[..., Any], *args: Any) -> Any:
        return func(*args)


SERVER = "github"
TOOL_NAMES = ("lightfall_list_git_repos", "lightfall_git_push")
GITHUB_ALLOWED_TOOLS = [tool_id(SERVER, n) for n in TOOL_NAMES]

ALLOW = "Allow"
DENY = "Deny"

_GATE = "Not pushed: pushing requires being logged in, online, with the system-admin permission."
_NOT_SIGNED_IN = ("Not pushed: not signed in to GitHub — ask the user to sign in "
                  "(Preferences → GitHub or the git-state indicator) and try again.")


def _default_checker() -> Any:
    from lightfall.updates.checker import get_update_checker

    return get_update_checker()


def _make_tools(*, permission_manager, agent_name, github_session, checker, target, push):
    from lightfall.agents.tooling import tool

    def _session() -> Any:
        return github_session if github_session is not None else get_github_session()

    def _read_checker() -> tuple[bool, list[tuple[str, Path, str, bool]]]:
        c = checker if checker is not None else _default_checker()
        repos = [(r.name, Path(r.root), r.branch, bool(r.dirty)) for r in c.result.repos]
        return bool(c.is_allowed()), repos

    async def _checker_state() -> tuple[bool, list[tuple[str, Path, str, bool]]]:
        # The checker's state belongs to the Qt thread; hop there as content_tools does.
        return await asyncio.to_thread(run_on_main_thread, _read_checker)

    async def _ask(header: str, question: str) -> str:
        """``"allow"``, ``"declined"`` or ``"no answer"`` (closed card or timeout)."""
        if permission_manager is None:
            return "no answer"
        answered, answers = await permission_manager.request_question([{
            "question": question,
            "header": header,
            "multiSelect": False,
            "options": [
                {"label": ALLOW, "description": "Push now, this once."},
                {"label": DENY, "description": "Don't push."},
            ],
        }])
        if not answered:
            return "no answer"
        return "allow" if answers.get(question) == ALLOW else "declined"

    def _fail(repo: str, branch: str, outcome: str, message: str) -> dict[str, Any]:
        logger.info("github_tools: push of {!r} ({}) by agent {}: {}",
                    repo, branch or "-", agent_name(), outcome)
        return mcp_error(scrub(message))

    @tool(
        name="lightfall_list_git_repos",
        description=(
            "List the git source checkouts Lightfall knows about (name, path, branch, "
            "dirty, and whether the current branch can be pushed to GitHub, with the "
            "reason if not), and whether the user is signed in to GitHub."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    async def list_git_repos(args: dict) -> dict[str, Any]:
        try:
            _, repos = await _checker_state()
            rows = []
            for name, root, branch, dirty in repos:
                found = await asyncio.to_thread(target, root)
                ok = not isinstance(found, str)
                rows.append({
                    "name": name, "path": str(root), "branch": branch, "dirty": dirty,
                    "eligible": ok, "reason": "" if ok else found,
                    "url": found.url if ok else "",
                })
            cred = _session().credential()
            return mcp_result({
                "repos": rows,
                "github": scrub(cred.masked, cred.token) if cred else "not signed in",
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("github_tools.list_git_repos failed")
            return mcp_error(scrub(f"lightfall_list_git_repos error: {exc}"))

    async def _resolve(repo: str, repos) -> Path | None:
        for name, root, _, _ in repos:
            if name == repo:
                return root
        try:
            top = await asyncio.to_thread(run_git, Path(repo), "rev-parse", "--show-toplevel")
        except Exception:  # noqa: BLE001 - missing path, not a repo, git missing
            return None
        return Path(top) if top else None

    @tool(
        name="lightfall_git_push",
        description=(
            "Push the current branch of a source checkout to its GitHub remote, using the "
            "user's GitHub sign-in. Give a repo name from lightfall_list_git_repos or a "
            "path. Never forces and pushes only the current branch. The user is asked and "
            "must allow each push; they may decline."
        ),
        input_schema={"type": "object",
                      "properties": {"repo": {"type": "string",
                                              "description": "Repo name or path."}},
                      "required": ["repo"]},
    )
    async def git_push(args: dict) -> dict[str, Any]:
        repo = str(args.get("repo") or "").strip()
        try:
            allowed, repos = await _checker_state()
            if not allowed:
                return _fail(repo, "", "gate closed", _GATE)
            root = await _resolve(repo, repos) if repo else None
            if root is None:
                return _fail(repo, "", "not a repo",
                             f"Not pushed: {repo} is not a git repository.")
            found = await asyncio.to_thread(target, root)
            if isinstance(found, str):
                return _fail(repo, "", "refused", f"Not pushed: {found}.")
            cred = _session().credential()
            if cred is None:
                return _fail(repo, found.branch, "not signed in", _NOT_SIGNED_IN)
            outcome = await _ask(
                "GitHub push",
                f"Push {found.branch} of {found.root} to {found.url} as {cred.login}?")
            if outcome != "allow":
                why = "the user declined" if outcome == "declined" else "no answer"
                return _fail(repo, found.branch, outcome, f"Not pushed: {why}.")
            result = await asyncio.to_thread(push, found, cred)
            if not result.ok:
                return _fail(repo, found.branch, "failed", scrub(result.message, cred.token))
            logger.info("github_tools: push of {!r} ({}) by agent {}: pushed",
                        repo, found.branch, agent_name())
            return mcp_result({
                "ok": True, "message": scrub(result.message, cred.token),
                "branch": found.branch, "url": found.url,
                "old_head": result.old_head, "new_head": result.new_head,
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("github_tools.git_push failed")
            return mcp_error(scrub(f"lightfall_git_push error: {exc}"))

    return [list_git_repos, git_push]


def create_github_tools_server(
    *,
    permission_manager: Any,
    agent_name: Callable[[], str],
    github_session: Any = None,
    checker: Any = None,
    target: Callable[..., Any] = push_target,
    push: Callable[..., Any] = push_repo,
):
    """The always-on ``github`` ToolServer for a session.

    Without a ``permission_manager`` every push is refused unasked. ``github_session`` and
    ``checker`` default to the process-wide ones.
    """
    from lightfall.agents.tooling import ToolServer, as_tool_spec

    tools = _make_tools(permission_manager=permission_manager, agent_name=agent_name,
                        github_session=github_session, checker=checker,
                        target=target, push=push)
    return ToolServer(name=SERVER, version="1.0.0", tools=tuple(as_tool_spec(t) for t in tools))


__all__ = ["GITHUB_ALLOWED_TOOLS", "create_github_tools_server"]
