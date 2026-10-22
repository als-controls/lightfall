"""MCP tools exposing skill drafting and the skill catalog to agents.

Every :class:`~lightfall.claude.agent.QtClaudeAgent` session gets a dedicated
``skills`` SDK MCP server (see ``create_skill_tools_server``) so agents can:

- propose new skills or revisions to existing ones (``draft_skill``). Drafts
  are never applied automatically -- a human must review and promote them
  out of ``_drafts/``;
- browse every skill Lightfall can resolve, including skills installed
  Python packages advertise through ``agent_skills`` entry points
  (``list_skills``: ids, descriptions and sources only);
- load one they weren't configured with (``get_skill``,
  ``get_skill_reference``). The user approves each such skill in the chat:
  once, or always for this agent (``lightfall.agents.skill_approvals``).
  Skills already in the session load without asking.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from lightfall.agents.tooling import tool_id
from lightfall.plugins.agents._mcp_helpers import mcp_error, mcp_result
from lightfall.utils.logging import logger

try:
    from lightfall.claude._internal.threading import run_on_main_thread
except ImportError:  # pragma: no cover - SDK-optional / headless environments
    def run_on_main_thread(func: Callable[..., Any], *args: Any) -> Any:
        return func(*args)


SKILLS_ALLOWED_TOOLS = [
    tool_id("skills", n)
    for n in ("draft_skill", "list_skills", "get_skill", "get_skill_reference")
]

#: Longest description or reason shown to the agent or the user, in characters.
DESCRIPTION_LIMIT = 300
#: Largest reference file ``get_skill_reference`` returns, in bytes.
REFERENCE_SIZE_LIMIT = 256 * 1024

ALLOW_ONCE = "Allow once"
DENY = "Deny"


def _always_label(agent: str) -> str:
    return f"Always allow for {agent}"


def _clip(text: str, limit: int = DESCRIPTION_LIMIT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def _source_label(info: Any) -> str:
    if info.source == "package":
        return f"from the installed Python package '{info.distribution or info.id.split(':')[0]}'"
    return {
        "builtin": "a built-in Lightfall skill",
        "beamline": "a beamline skill",
        "plugin": "a Lightfall plugin's skill",
        "user": "one of your user skills",
    }.get(info.source, info.source)


def _matches(info: Any, query: str) -> bool:
    haystack = f"{info.id} {info.description}".lower()
    return all(term in haystack for term in query.lower().split())


def _in_session(info: Any, session_dir: Path | None) -> bool:
    """Whether `info` is the skill already materialized into this session."""
    from lightfall.agents.skills_store import resolve_skills

    if session_dir is None:
        return False
    if not (Path(session_dir) / "skills" / info.name / "SKILL.md").is_file():
        return False
    origin = resolve_skills().get(info.name)
    return origin is not None and Path(origin).resolve() == Path(info.path).resolve()


def _make_tools(
    author_name: Callable[[], str],
    session_id: Callable[[], str | None],
    *,
    agent_name: Callable[[], str] | None = None,
    session_plugin_dir: Callable[[], Path | None] | None = None,
    permission_manager: Any = None,
):
    """Build the ``skills`` server's ``@tool`` callables.

    Args:
        author_name: Callable returning this session's current author/bus
            name (read live at call time).
        session_id: Callable returning this session's current session id
            (or ``None``).
        agent_name: Callable returning the agent (spec) name that "always
            allow" approvals are recorded under. Defaults to `author_name`.
        session_plugin_dir: Callable returning the session's plugin dir, whose
            ``skills/`` holds the skills already given to this session.
        permission_manager: Object with ``async request_question(questions)``
            (a :class:`~lightfall.claude.permission_manager.PermissionManager`)
            used to ask the user to approve a skill. Without it, only skills
            already in the session or approved for the agent can be loaded.
    """
    from lightfall.agents.tooling import tool

    agent_of = agent_name or author_name
    plugin_dir_of = session_plugin_dir or (lambda: None)
    allowed_this_session: set[str] = set()

    def _available(info: Any) -> bool:
        from lightfall.agents.skill_approvals import approved_skill_ids

        return (
            info.id in allowed_this_session
            or info.id in approved_skill_ids(agent_of())
            or _in_session(info, plugin_dir_of())
        )

    @tool(
        name="draft_skill",
        description=(
            "Draft a new skill, or propose a revision to an existing one. "
            "The draft is saved for human review and does not take effect "
            "until a human approves it by moving it out of _drafts/."
        ),
        input_schema={
            "name": {
                "type": "string",
                "description": "Skill name (lowercase alphanumeric, hyphens, underscores).",
            },
            "description": {
                "type": "string",
                "description": "One-line human-readable description of the skill.",
            },
            "body": {
                "type": "string",
                "description": "Skill body content (markdown).",
            },
        },
    )
    async def draft_skill(args: dict) -> dict[str, Any]:
        try:
            from lightfall.agents.drafts import DraftError, save_draft

            name = args.get("name", "")
            description = args.get("description", "")
            body = args.get("body", "")

            def _save() -> tuple[str, Any, bool]:
                # Caught HERE, on the main-thread side of the hop, rather
                # than relying on DraftError propagating back across
                # run_on_main_thread: the real implementation rewraps any
                # exception raised on the marshalled path as a generic
                # RuntimeError with a traceback dump, which would otherwise
                # bury the clean validation message.
                try:
                    path, is_revision = save_draft(
                        name,
                        description,
                        body,
                        author=author_name(),
                        session_id=session_id(),
                    )
                except DraftError as exc:
                    return ("invalid", str(exc), False)
                return ("ok", path, is_revision)

            status, payload, is_revision = run_on_main_thread(_save)
            if status == "invalid":
                return mcp_error(payload)
            path = payload

            message = (
                f"Draft saved to {path}. A human must approve it "
                "(move it out of _drafts/) before it takes effect."
            )
            if is_revision:
                message += (
                    f" This is a proposed REVISION of the active skill '{name}'."
                )

            _fire_toast(name, author_name())

            return mcp_result(message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("skill_tools.draft_skill failed")
            return mcp_error(f"draft_skill error: {exc}")

    @tool(
        name="list_skills",
        description=(
            "List the skills Lightfall can give you, with each one's id, a short "
            "description of when to use it, its source, and whether you can load it "
            "without asking. Includes skills that installed Python packages provide "
            "(ids like 'gpcam:kernel-designer'). Pass a query to keep only skills whose "
            "id or description contains every word of it. Use get_skill to read one."
        ),
        input_schema={
            "query": {
                "type": "string",
                "description": "Optional search words, e.g. 'kernel periodic'.",
            },
        },
    )
    async def list_skills(args: dict) -> dict[str, Any]:
        try:
            from lightfall.agents.skills_store import skill_catalog

            query = str(args.get("query") or "")
            rows = []
            for info in skill_catalog().values():
                if query and not _matches(info, query):
                    continue
                rows.append({
                    "id": info.id,
                    "description": _clip(info.description) or "(no description)",
                    "source": info.source,
                    "package": info.distribution,
                    "available": _available(info),
                })
            return mcp_result({
                "skills": rows,
                "note": (
                    "Descriptions come from each skill's author. Skills with "
                    "available=false need the user's approval: get_skill asks them."
                ),
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("skill_tools.list_skills failed")
            return mcp_error(f"list_skills error: {exc}")

    @tool(
        name="get_skill",
        description=(
            "Read a skill's full instructions (its SKILL.md) by id, as listed by "
            "list_skills. If the skill isn't already available to you, the user is "
            "asked to approve it first, and sees your reason; they may decline. "
            "Only ask for a skill you expect to use for the current task."
        ),
        input_schema={
            "id": {"type": "string", "description": "Skill id from list_skills."},
            "reason": {
                "type": "string",
                "description": "One sentence on why you need this skill now (shown to the user).",
            },
        },
    )
    async def get_skill(args: dict) -> dict[str, Any]:
        try:
            from lightfall.agents.skill_approvals import approve_skill
            from lightfall.agents.skills_store import (
                render_skill_md,
                skill_catalog,
                template_variables,
            )

            skill_id = str(args.get("id") or "")
            info = skill_catalog().get(skill_id)
            if info is None:
                return mcp_error(f"No skill with id '{skill_id}'. Call list_skills for ids.")

            if not _available(info):
                if permission_manager is None:
                    return mcp_error(
                        f"Skill '{skill_id}' needs the user's approval, and this session "
                        "can't ask for it. Ask the user to add it to your skills."
                    )
                agent = agent_of()
                question = (
                    f"Agent '{agent}' wants to load the skill '{info.id}', "
                    f"{_source_label(info)}. "
                    f"Description: {_clip(info.description) or '(none)'} "
                    f"Reason given: {_clip(args.get('reason') or '(none)')}"
                )
                always = _always_label(agent)
                answered, answers = await permission_manager.request_question([{
                    "question": question,
                    "header": "Load skill",
                    "multiSelect": False,
                    "options": [
                        {"label": ALLOW_ONCE, "description": "For this session only."},
                        {"label": always,
                         "description": "Also load it into this agent's future sessions."},
                        {"label": DENY, "description": "Don't load it."},
                    ],
                }])
                choice = answers.get(question) if answered else None
                if choice == always:
                    approve_skill(agent, info.id)
                elif choice != ALLOW_ONCE:
                    logger.info("skill_tools: user declined skill '{}' for '{}'", info.id, agent)
                    return mcp_error(
                        f"The user did not approve loading '{info.id}'. "
                        "Continue without it, or ask them why."
                    )
                allowed_this_session.add(info.id)

            text = (Path(info.path) / "SKILL.md").read_text(encoding="utf-8")
            text = render_skill_md(text, template_variables(), origin=info.path / "SKILL.md")
            refs = sorted(
                f.relative_to(info.path).as_posix()
                for f in Path(info.path).rglob("*")
                if f.is_file() and f.name != "SKILL.md"
            )
            footer = (
                "\n\nReference files (read with get_skill_reference): " + ", ".join(refs)
                if refs else ""
            )
            return mcp_result(f"Skill '{info.id}' ({_source_label(info)}):\n\n{text}{footer}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("skill_tools.get_skill failed")
            return mcp_error(f"get_skill error: {exc}")

    @tool(
        name="get_skill_reference",
        description=(
            "Read one of a skill's reference files, by skill id and the relative path "
            "get_skill listed. Only works for skills you can already load."
        ),
        input_schema={
            "id": {"type": "string", "description": "Skill id from list_skills."},
            "path": {
                "type": "string",
                "description": "Path inside the skill, e.g. 'references/notes.md'.",
            },
        },
    )
    async def get_skill_reference(args: dict) -> dict[str, Any]:
        try:
            from lightfall.agents.skills_store import skill_catalog

            skill_id = str(args.get("id") or "")
            info = skill_catalog().get(skill_id)
            if info is None:
                return mcp_error(f"No skill with id '{skill_id}'. Call list_skills for ids.")
            if not _available(info):
                return mcp_error(
                    f"Skill '{skill_id}' isn't available to you yet; call get_skill first."
                )
            root = Path(info.path).resolve()
            target = (root / str(args.get("path") or "")).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                return mcp_error(f"No file '{args.get('path')}' in skill '{skill_id}'.")
            if target.stat().st_size > REFERENCE_SIZE_LIMIT:
                return mcp_error(f"'{args.get('path')}' is too large to return.")
            try:
                return mcp_result(target.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                return mcp_error(f"'{args.get('path')}' is not a text file.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("skill_tools.get_skill_reference failed")
            return mcp_error(f"get_skill_reference error: {exc}")

    return [draft_skill, list_skills, get_skill, get_skill_reference]


def _fire_toast(name: str, author: str) -> None:
    try:
        def _show() -> None:
            from lightfall.ui.toast import ToastManager

            mgr = ToastManager.get_instance()
            mgr.info(
                f"Skill draft: {name}",
                f"drafted by {author} — pending approval",
            )

        run_on_main_thread(_show)
    except Exception:  # noqa: BLE001 — never let a toast failure break the run
        logger.exception("skill_tools toast failed")


def create_skill_tools_server(
    author_name: Callable[[], str],
    session_id: Callable[[], str | None],
    *,
    agent_name: Callable[[], str] | None = None,
    session_plugin_dir: Callable[[], Path | None] | None = None,
    permission_manager: Any = None,
):
    """Create the always-on ``skills`` ToolServer for a session.

    Args:
        author_name: Callable returning the session's current author/bus name.
        session_id: Callable returning the session's current session id.
        agent_name, session_plugin_dir, permission_manager: see ``_make_tools``.

    Returns:
        ToolServer with the ``draft_skill``, ``list_skills``, ``get_skill`` and
        ``get_skill_reference`` tools.
    """
    from lightfall.agents.tooling import ToolServer, as_tool_spec

    tools = _make_tools(
        author_name,
        session_id,
        agent_name=agent_name,
        session_plugin_dir=session_plugin_dir,
        permission_manager=permission_manager,
    )
    return ToolServer(
        name="skills",
        version="1.1.0",
        tools=tuple(as_tool_spec(t) for t in tools),
    )


__all__ = ["create_skill_tools_server", "_make_tools", "SKILLS_ALLOWED_TOOLS"]
