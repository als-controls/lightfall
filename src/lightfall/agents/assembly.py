"""Session-time assembly of spec-driven agent options.

Bridges ``AgentSpec`` (Task 1) + ``AgentSpecRegistry`` (Task 2) +
``ToolRegistry`` (Task 3) + ``skills_store`` (Task 4) into the inputs
``QtClaudeAgent`` needs to construct a ``ClaudeAgentOptions``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from lightfall.agents.skills_store import materialize_skills
from lightfall.agents.skills_store import template_variables as _skills_store_template_variables
from lightfall.agents.spec import AgentSpec, AgentSpecError, resolve_template
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.agents.registry import AgentSpecRegistry
    from lightfall.ui.panels.claude.tool_registry import ToolRegistry


def template_variables() -> dict[str, str]:
    """Live ``{{variable}}`` substitution values.

    Delegates to ``lightfall.agents.skills_store.template_variables`` --
    that is the canonical implementation (beamline from the
    ``tiled_beamline`` preference, user via ``getpass``, endstation "").
    """
    return _skills_store_template_variables()


def agent_cwd(spec: AgentSpec) -> str:
    """Stable per-agent working directory for the Claude agent subprocess.

    ``~/lightfall`` for the primary "lightfall" agent (preserves existing
    session history); ``~/lightfall/agents/<name>`` for every other agent.
    Created if missing.
    """
    if spec.name == "lightfall":
        path = Path.home() / "lightfall"
    else:
        path = Path.home() / "lightfall" / "agents" / spec.name
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def subagent_definitions(
    current: AgentSpec,
    registry: AgentSpecRegistry,
    variables: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build SDK ``AgentDefinition`` entries for every OTHER enabled spec
    with ``subagent=True``.

    Returns ``{}`` (with a log entry) if the SDK's ``AgentDefinition`` type
    cannot be imported. Any spec whose prompt fails template resolution
    (e.g. an unknown ``{{var}}`` that bypassed load-time validation) is
    logged and skipped rather than raised -- a bad agent file must never
    break session construction for every OTHER enabled spec.
    """
    try:
        from claude_agent_sdk.types import AgentDefinition
    except ImportError:
        logger.debug("AgentDefinition not available; skipping subagent definitions")
        return {}

    if variables is None:
        variables = template_variables()
    defs: dict[str, Any] = {}
    for spec in registry.enabled_specs():
        if spec.name == current.name or not spec.subagent:
            continue
        try:
            defs[spec.name] = AgentDefinition(
                description=spec.description,
                prompt=resolve_template(spec.prompt, variables),
                model=spec.model,
                effort=spec.effort,
                memory=("project" if spec.memory else None),
            )
        except AgentSpecError as exc:
            logger.warning("agent '{}': skipping subagent definition: {}", spec.name, exc)
    return defs


def assemble_spec_options(
    spec: AgentSpec,
    tool_registry: ToolRegistry,
    spec_registry: AgentSpecRegistry,
    session_plugin_dir: Path,
) -> dict[str, Any]:
    """Assemble the spec-driven portion of ``ClaudeAgentOptions``.

    Returns a partial options dict with ``system_prompt``, ``cwd``,
    ``agents``, ``mcp_servers``, and ``allowed_tools``. Callers merge this
    with any always-on tools/servers (e.g. the ``qt`` MCP server).
    """
    from lightfall.claude._session_assembly import assemble_mcp_servers

    variables = template_variables()
    system_prompt = resolve_template(spec.prompt, variables)

    if spec.memory is False and spec.name == "lightfall":
        logger.warning(
            "agent '{}': memory: false is a no-op for the top-level session in v1; "
            "it is only honored for subagent roles",
            spec.name,
        )

    wanted_tools = set(spec.tools)
    available = {p.name: p for p in tool_registry.enabled_plugins()}
    unknown = wanted_tools - set(available)
    for name in sorted(unknown):
        logger.warning("agent '{}': unknown tool plugin '{}' in spec.tools", spec.name, name)

    filtered_plugins = [p for p in tool_registry.enabled_plugins() if p.name in wanted_tools]
    mcp_servers, allowed_tools = assemble_mcp_servers(filtered_plugins)

    materialize_skills(spec.skills, session_plugin_dir, variables=variables)

    agents = subagent_definitions(spec, spec_registry, variables=variables)

    return {
        "system_prompt": system_prompt,
        "cwd": agent_cwd(spec),
        "agents": agents,
        "mcp_servers": mcp_servers,
        "allowed_tools": allowed_tools,
    }
