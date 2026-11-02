"""MCP tools that let an agent list, publish, retract and restore shared content.

Every session gets the always-on ``content`` server (``create_content_tools_server``).
Listing and history are read-only. Publish, retract and restore always ask the user
first in the chat, offering only Allow and Deny (no "always allow"). Anything but Allow
sends nothing. Each write carries ``X-Lightfall-Agent: 1``, and a publish manifest says
``agent: true``.

Handlers run on the agent worker's asyncio loop: bundling, git and HTTP go to a thread
(``asyncio.to_thread``), and registry reads hop to the Qt thread. No widgets are touched.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from lightfall.agents.tooling import tool_id
from lightfall.content import actions
from lightfall.content.labels import describe_source
from lightfall.content.publish import (
    MAX_AUTHOR_CHARS,
    MAX_MESSAGE_CHARS,
    _human,
    build_bundle,
    policy_problems,
)
from lightfall.content.publish import (
    local_items as _default_local_items,
)
from lightfall.content.store import CONTENT_TYPES, _is_shared_scope
from lightfall.plugins.agents._mcp_helpers import mcp_error, mcp_result
from lightfall.scopes.client import ScopesClient, ScopesError, describe_error
from lightfall.utils.logging import logger

try:
    from lightfall.claude._internal.threading import run_on_main_thread
except ImportError:  # pragma: no cover - SDK-optional / headless environments
    def run_on_main_thread(func: Callable[..., Any], *args: Any) -> Any:
        return func(*args)


SERVER = "content"
TOOL_NAMES = ("lightfall_list_content", "lightfall_content_history",
              "lightfall_publish_content", "lightfall_retract_content",
              "lightfall_restore_content")
CONTENT_ALLOWED_TOOLS = [tool_id(SERVER, n) for n in TOOL_NAMES]

ALLOW = "Allow"
DENY = "Deny"

_TYPE_SCHEMA = {"type": "string", "enum": list(CONTENT_TYPES),
                "description": "plan, plugin, agent or skill."}
_NAME_SCHEMA = {"type": "string", "description": "Item name, as lightfall_list_content shows it."}
_SCOPE_SCHEMA = {"type": "string",
                 "description": "Shared scope id, e.g. 'branch:7.0.1.1', 'beamline:7.0.1' "
                                "or 'facility' (see publishable_scopes)."}


def _rows_on_main_thread() -> list[actions.ContentRow]:
    # content_rows reads the loaders' registries, which live on the Qt thread.
    return run_on_main_thread(actions.content_rows)


def _schema(properties: dict[str, Any], required: Sequence[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required)}


def _make_tools(
    *,
    permission_manager: Any,
    agent_name: Callable[[], str],
    author_name: Callable[[], str],
    session_id: Callable[[], str | None],
    client_factory: Callable[[], Any],
    scope_state: Callable[[], tuple[Sequence[str], dict[str, str | None], Any]],
    local_items: Callable[[], list[Any]],
    rows: Callable[[], list[actions.ContentRow]],
):
    from lightfall.agents.tooling import tool

    async def _ask(header: str, question: str) -> str | None:
        """``"allow"``, ``"declined"`` or ``"no answer"`` (closed card or timeout)."""
        if permission_manager is None:
            return "no answer"
        answered, answers = await permission_manager.request_question([{
            "question": question,
            "header": header,
            "multiSelect": False,
            "options": [
                {"label": ALLOW, "description": "Do it now, this once."},
                {"label": DENY, "description": "Don't do it."},
            ],
        }])
        if not answered:
            return "no answer"
        return "allow" if answers.get(question) == ALLOW else "declined"

    def _not_done(verb: str, outcome: str, type_: str, name: str) -> dict[str, Any]:
        logger.info("content_tools: {} {!r} not {} for agent {} ({}) in session {} ({})",
                    type_, name, verb, agent_name(), author_name(), session_id(), outcome)
        why = "the user declined" if outcome == "declined" else "no answer"
        return mcp_error(f"Not {verb}: {why}.")

    def _check(args: dict, roles: dict[str, str | None]) -> tuple[str, str, str] | str:
        """``(type, name, scope)``, or the reason the request can't go ahead."""
        type_ = str(args.get("type") or "")
        name = str(args.get("name") or "")
        scope = str(args.get("scope") or "")
        if type_ not in CONTENT_TYPES:
            return f"Unknown type '{type_}'; use one of {', '.join(CONTENT_TYPES)}."
        if not name:
            return "A name is required."
        if not _is_shared_scope(scope):
            return f"'{scope}' is not a shared scope (facility, beamline:X or branch:X)."
        if not actions._role_at_least(roles.get(scope), actions.PUBLISH_ROLE):
            return (f"You're not a maintainer of {describe_source(scope)}, so nothing can "
                    "be published, retracted or restored there.")
        return type_, name, scope

    def _write_error(exc: Exception, scope: str) -> str:
        return describe_error(exc, scope=scope) if isinstance(exc, ScopesError) else str(exc)

    async def _history(client: Any, scope: str, type_: str, name: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(actions.history, client, scope, type_, name)

    @tool(
        name="lightfall_list_content",
        description=(
            "List the plans, plugins, agents and skills Lightfall has loaded, with where "
            "each comes from (source: 'local' is the user's own, or a shared scope such as "
            "'branch:7.0.1.1') and its shared revision. Also lists the user's own local "
            "items (the ones that can be published) and the scopes the user may publish to."
        ),
        input_schema=_schema({"type": {**_TYPE_SCHEMA,
                                       "description": "Optional: only this type."}}, ()),
    )
    async def list_content(args: dict) -> dict[str, Any]:
        try:
            only = args.get("type") or None
            if only is not None and only not in CONTENT_TYPES:
                return mcp_error(f"Unknown type '{only}'; use one of {', '.join(CONTENT_TYPES)}.")
            chain, roles, _ = scope_state()
            found = await asyncio.to_thread(rows)
            mine = await asyncio.to_thread(local_items)
            return mcp_result({
                "items": [{"type": r.type, "name": r.name, "source": r.source,
                           "source_label": describe_source(r.source), "revision": r.revision}
                          for r in found if only in (None, r.type)],
                "local_items": [{"type": i.type, "name": i.name}
                                for i in mine if only in (None, i.type)],
                "publishable_scopes": [{"scope": s, "label": describe_source(s)}
                                       for s in actions.publishable_scopes(chain, roles)],
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("content_tools.list_content failed")
            return mcp_error(f"lightfall_list_content error: {exc}")

    @tool(
        name="lightfall_content_history",
        description="Revision history of a shared item at one scope (read-only).",
        input_schema=_schema({"type": _TYPE_SCHEMA, "name": _NAME_SCHEMA,
                              "scope": _SCOPE_SCHEMA}, ("type", "name", "scope")),
    )
    async def content_history(args: dict) -> dict[str, Any]:
        type_, name, scope = (str(args.get(k) or "") for k in ("type", "name", "scope"))
        if type_ not in CONTENT_TYPES or not name or not _is_shared_scope(scope):
            return mcp_error("type, name and a shared scope are required.")
        try:
            records = await _history(client_factory(), scope, type_, name)
        except ScopesError as exc:
            return mcp_error(str(exc) if exc.status is not None else describe_error(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("content_tools.content_history failed")
            return mcp_error(f"lightfall_content_history error: {exc}")
        return mcp_result({"type": type_, "name": name, "scope": scope,
                           "scope_label": describe_source(scope), "history": records})

    @tool(
        name="lightfall_publish_content",
        description=(
            "Publish one of the user's local plans, plugins, agents or skills to a shared "
            "scope as a new revision. The user is shown exactly what will be uploaded and "
            "must allow it; they may decline. Only scopes the user maintains are allowed. "
            "Give 'author' only when publishing someone else's work."
        ),
        input_schema=_schema({
            "type": _TYPE_SCHEMA, "name": _NAME_SCHEMA, "scope": _SCOPE_SCHEMA,
            "message": {"type": "string", "description": "What changed (shown in history)."},
            "author": {"type": "string",
                       "description": "Optional: the person who wrote it, if not the user."},
        }, ("type", "name", "scope", "message")),
    )
    async def publish_content(args: dict) -> dict[str, Any]:
        chain, roles, policy = scope_state()
        checked = _check(args, roles)
        if isinstance(checked, str):
            return mcp_error(checked)
        type_, name, scope = checked
        label = describe_source(scope)
        message = str(args.get("message") or "").strip()
        author = " ".join(str(args.get("author") or "").split()) or None
        if not message:
            return mcp_error("A message saying what changed is required.")
        if len(message) > MAX_MESSAGE_CHARS:
            return mcp_error(f"The message is too long ({len(message)} characters; "
                             f"the limit is {MAX_MESSAGE_CHARS}).")
        if author is not None and len(author) > MAX_AUTHOR_CHARS:
            return mcp_error(f"The author is too long ({len(author)} characters; "
                             f"the limit is {MAX_AUTHOR_CHARS}).")
        try:
            mine = await asyncio.to_thread(local_items)
        except Exception as exc:  # noqa: BLE001
            logger.exception("content_tools: listing local items failed")
            return mcp_error(f"Could not list your local content: {exc}")
        item = next((i for i in mine if (i.type, i.name) == (type_, name)), None)
        if item is None:
            return mcp_error(f"No local {type_} named '{name}' in ~/lightfall; only the "
                             "user's own items can be published.")

        tmp = Path(tempfile.mkdtemp(prefix="lightfall-agent-publish-"))
        try:
            try:
                bundle = await asyncio.to_thread(build_bundle, item, tmp)
            except Exception as exc:  # noqa: BLE001
                logger.exception("content_tools: bundling {} {} failed", type_, name)
                return mcp_error(f"Could not bundle {type_} '{name}': {exc}")
            problems = policy_problems(bundle, policy)
            if problems and not actions.is_admin(scope, roles):
                return mcp_error(f"Not published: {type_} '{name}' is over the limits of "
                                 f"{label}: " + "; ".join(problems) + ".")
            question = (
                f"The assistant wants to publish {type_} '{name}' to {label}: "
                f"{bundle.file_count} files, {_human(bundle.size)}, "
                f"sha {bundle.sha256[:12]}. Message: {' '.join(message.split())}"
            )
            if author is not None:
                question += f" Author: {author}"
            outcome = await _ask("Publish content", question)
            if outcome != "allow":
                return _not_done("published", outcome, type_, name)
            try:
                record = await asyncio.to_thread(
                    actions.publish, client_factory(), item, bundle, scope,
                    message=message, author=author, agent=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("content_tools: publish {} {} to {} failed: {}",
                               type_, name, scope, exc)
                return mcp_error(f"Not published: {_write_error(exc, scope)}")
            return mcp_result(f"Published {type_} '{name}' to {label} as revision "
                              f"{record.get('revision')}.")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @tool(
        name="lightfall_retract_content",
        description=(
            "Take a shared item down at one scope (its revisions stay in the history and "
            "can be restored). The user must allow it; they may decline."
        ),
        input_schema=_schema({"type": _TYPE_SCHEMA, "name": _NAME_SCHEMA,
                              "scope": _SCOPE_SCHEMA}, ("type", "name", "scope")),
    )
    async def retract_content(args: dict) -> dict[str, Any]:
        _, roles, _ = scope_state()
        checked = _check(args, roles)
        if isinstance(checked, str):
            return mcp_error(checked)
        type_, name, scope = checked
        label = describe_source(scope)
        client = client_factory()
        try:
            records = await _history(client, scope, type_, name)
        except Exception as exc:  # noqa: BLE001
            return mcp_error(f"Not retracted: {_write_error(exc, scope)}")
        current = next((r.get("revision") for r in records if r.get("current")), None)
        if current is None:
            return mcp_error(f"Not retracted: {type_} '{name}' isn't currently published "
                             f"at {label}.")
        question = (f"The assistant wants to retract {type_} '{name}' from {label} "
                    f"(current revision {current}). Its revisions stay in the history "
                    "and can be restored.")
        outcome = await _ask("Retract content", question)
        if outcome != "allow":
            return _not_done("retracted", outcome, type_, name)
        try:
            await asyncio.to_thread(actions.retract, client, scope, type_, name, agent=True)
        except Exception as exc:  # noqa: BLE001
            return mcp_error(f"Not retracted: {_write_error(exc, scope)}")
        return mcp_result(f"Retracted {type_} '{name}' from {label} (was revision {current}).")

    @tool(
        name="lightfall_restore_content",
        description=(
            "Make an earlier revision of a shared item current again at one scope (see "
            "lightfall_content_history). The user must allow it; they may decline."
        ),
        input_schema=_schema({"type": _TYPE_SCHEMA, "name": _NAME_SCHEMA,
                              "scope": _SCOPE_SCHEMA,
                              "revision": {"type": "integer",
                                           "description": "Revision to make current."}},
                             ("type", "name", "scope", "revision")),
    )
    async def restore_content(args: dict) -> dict[str, Any]:
        _, roles, _ = scope_state()
        checked = _check(args, roles)
        if isinstance(checked, str):
            return mcp_error(checked)
        type_, name, scope = checked
        label = describe_source(scope)
        try:
            target = int(args.get("revision"))
        except (TypeError, ValueError):
            return mcp_error("revision must be a whole number.")
        client = client_factory()
        try:
            records = await _history(client, scope, type_, name)
        except Exception as exc:  # noqa: BLE001
            return mcp_error(f"Not restored: {_write_error(exc, scope)}")
        known = {r.get("revision") for r in records}
        if target not in known:
            return mcp_error(f"Not restored: {type_} '{name}' has no revision {target} at "
                             f"{label}.")
        current = next((r.get("revision") for r in records if r.get("current")), None)
        if current == target:
            return mcp_error(f"Revision {target} is already current at {label}.")
        now = f"revision {current}" if current is not None else "nothing (retracted)"
        question = (f"The assistant wants to restore {type_} '{name}' at {label}: "
                    f"current {now} → revision {target}.")
        outcome = await _ask("Restore content", question)
        if outcome != "allow":
            return _not_done("restored", outcome, type_, name)
        try:
            await asyncio.to_thread(actions.restore, client, scope, type_, name, target,
                                    agent=True)
        except Exception as exc:  # noqa: BLE001
            return mcp_error(f"Not restored: {_write_error(exc, scope)}")
        return mcp_result(f"Restored {type_} '{name}' at {label} to revision {target}.")

    return [list_content, content_history, publish_content, retract_content, restore_content]


def create_content_tools_server(
    *,
    permission_manager: Any,
    agent_name: Callable[[], str],
    author_name: Callable[[], str],
    session_id: Callable[[], str | None],
    client_factory: Callable[[], Any] = ScopesClient,
    scope_state: Callable[[], tuple[Sequence[str], dict[str, str | None], Any]] | None = None,
    local_items: Callable[[], list[Any]] | None = None,
    rows: Callable[[], list[actions.ContentRow]] | None = None,
):
    """The always-on ``content`` ToolServer for a session.

    ``permission_manager`` asks the user (``async request_question``); without one every
    write is refused unasked. ``scope_state``, ``local_items`` and ``rows`` default to the
    live scope state, ``~/lightfall``'s items and the loaders' rows (read on the Qt thread).
    """
    from lightfall.agents.tooling import ToolServer, as_tool_spec

    tools = _make_tools(
        permission_manager=permission_manager,
        agent_name=agent_name,
        author_name=author_name,
        session_id=session_id,
        client_factory=client_factory,
        scope_state=scope_state or actions.current_scope_state,
        local_items=local_items or _default_local_items,
        rows=rows or _rows_on_main_thread,
    )
    return ToolServer(name=SERVER, version="1.0.0", tools=tuple(as_tool_spec(t) for t in tools))


__all__ = ["CONTENT_ALLOWED_TOOLS", "ALLOW", "DENY", "create_content_tools_server"]
