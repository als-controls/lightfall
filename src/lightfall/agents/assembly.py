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


def identity_preamble(spec: AgentSpec, registry: AgentSpecRegistry) -> str:
    """Markdown preamble telling ``spec`` who it is and how to reach peers.

    Lists every OTHER enabled spec in ``registry``, tagging each with
    ``[openable as a session]`` and/or ``[Agent-tool delegable]`` depending
    on its ``openable``/``subagent`` flags. Callers should treat any failure
    here as non-fatal (see ``assemble_spec_options``).
    """
    peer_lines: list[str] = []
    for peer in registry.enabled_specs():
        if peer.name == spec.name:
            continue
        tags = []
        if peer.openable:
            tags.append("openable as a session")
        if peer.subagent:
            tags.append("Agent-tool delegable")
        tag_suffix = f" [{', '.join(tags)}]" if tags else ""
        peer_lines.append(f"- **{peer.name}** — {peer.description}{tag_suffix}")

    peers_section = "\n".join(peer_lines) if peer_lines else "- (no other agents are currently enabled)"

    return f"""## Your identity

You are **{spec.name}** — {spec.description}. You are one of several defined agents in this
Lightfall installation, running as a session in the Claude panel's tab bar. Your
name on the agent bus is "{spec.name}".

### Peer agents defined here

{peers_section}

### Working with peers

- `mcp__bus__list_agents` shows which peers are RUNNING right now; `mcp__bus__send_message`
  messages a running peer (delivery honors their accept policy).
- The peer list above is a snapshot from session start; call `mcp__bus__list_agents` for the
  live roster, including agents defined after this session began.
- To LAUNCH a peer that isn't running, open its session tab:
  `lightfall_invoke_panel_action(panel_id="lightfall.panels.claude", action="open_agent_tab", kwargs={{"agent": "<name>", "message": "optional first message"}})`.
  Sessions are otherwise opened by the user via the panel's "+" button.
- Peers marked Agent-tool delegable also appear as agent types in your Agent tool for
  background subagent runs (isolated, report-back; no session tab)."""


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

    try:
        system_prompt = identity_preamble(spec, spec_registry) + "\n\n" + system_prompt
    except Exception as exc:  # noqa: BLE001 - preamble is best-effort, never breaks assembly
        logger.warning("agent '{}': skipping identity preamble: {}", spec.name, exc)

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

    try:
        agents = subagent_definitions(spec, spec_registry, variables=variables)
    except Exception as exc:  # noqa: BLE001 - a broken registry must not break assembly
        logger.warning("agent '{}': skipping subagent definitions: {}", spec.name, exc)
        agents = {}

    return {
        "system_prompt": system_prompt,
        "cwd": agent_cwd(spec),
        "agents": agents,
        "mcp_servers": mcp_servers,
        "allowed_tools": allowed_tools,
    }
