# AgentPlugin

## Purpose

Agent plugins extend the embedded Claude agent. One `AgentPlugin` can contribute, when enabled:

- a **skill** — a system-prompt snippet materialized as a `SKILL.md` file in the per-session SDK plugin directory (via `get_system_prompt()`), optionally with supplementary reference documents loaded lazily on demand (via `get_references_dir()`);
- a **bag of MCP tools** — an in-process MCP server assembled from `@tool`-decorated callables (via `create_tools()`).

A plugin may provide either or both; one settings toggle controls a plugin's prompt and tools together.

## Base Class

```python
from lightfall.plugins.agent_plugin import AgentPlugin
```

| Class attribute | Value |
|-----------------|-------|
| `type_name` | `"agent"` |
| `is_singleton` | `True` |

## Required Properties

```python
@property
def name(self) -> str: ...

@property
def description(self) -> str: ...
```

- **`name`** — unique plugin identifier, at most 64 characters, lowercase with hyphens/underscores. It is used as the manifest entry name, the `SKILL.md` frontmatter `name` (underscores are converted to hyphens at materialization), the MCP server name (tools become `mcp__<name>__<tool_name>`), and the settings-UI preference identifier.
- **`description`** — one-line description shown in the settings UI and used as the `SKILL.md` frontmatter `description`. Truncated to 1024 characters at materialization (an SDK limit); a warning is logged if truncation occurs.

## Optional Properties

| Property | Default | Purpose |
|----------|---------|---------|
| `display_name` | `name` title-cased with underscores as spaces | Human-readable name in the settings UI |
| `category` | `"general"` | Settings-UI grouping; common values: `general`, `devices`, `acquisition`, `operations`, `development` |
| `enabled_by_default` | `True` | Whether the plugin is active before the user touches the toggle |
| `priority` | `100` | Sort order (lower = first) in the settings UI and in session assembly |

## Optional Methods

```python
def get_system_prompt(self) -> str:
    """Return the SKILL.md body. Empty string = no skill contribution."""
    return ""

def create_tools(self) -> list[Any]:
    """Return @tool-decorated callables. Empty = no MCP server contribution."""
    return []

def get_references_dir(self) -> Path | None:
    """Optional package directory of supplementary docs, copied to
    references/ next to the SKILL.md and loaded lazily by the SDK Skill tool."""
    return None
```

Tools are created with the `claude_agent_sdk.tool` decorator, which takes a `name`, `description`, and JSON `input_schema`, and wraps an `async` function receiving the arguments dict. Tool results must use the MCP content format; the helpers in `lightfall.plugins.agents._mcp_helpers` (`mcp_result`, `mcp_error`) produce it. Tools that touch Qt objects must hop to the main thread (`lightfall.claude._internal.threading.run_on_main_thread`).

## Lifecycle: how a session is assembled

1. **Registration.** `PluginLoader` instantiates each manifest entry with `type_name="agent"` and registers the instance with `AgentRegistry` (`lightfall.ui.panels.claude.agent_registry`).
2. **Enablement.** `AgentRegistry.enabled_plugins()` applies the user's toggles from the *Claude Tools* settings page, returning plugins sorted by `priority`. Semantics are opt-out: a plugin is enabled when its name is not in the `disabled_tool_plugins` preference and either `enabled_by_default` is true or its name is in `forced_enabled_tool_plugins` — so newly installed plugins take their declared default.
3. **Session assembly.** At agent construction time (`QtClaudeAgent.__init__`, implemented in `lightfall.claude._session_assembly`):
   - A fresh temporary SDK plugin directory is created with a minimal `plugin.json`.
   - For each enabled plugin with a non-empty prompt, `materialize_skill()` writes `skills/<name>/SKILL.md` (frontmatter `name` + `description`, then the prompt body) and copies `get_references_dir()` to `skills/<name>/references/` if provided. References are not injected into the prompt — the SDK's Skill tool loads them on demand when the skill is invoked.
   - For each enabled plugin with tools, `assemble_mcp_servers()` calls `create_sdk_mcp_server(name=plugin.name, ...)` and adds `mcp__<plugin.name>__<tool_name>` entries to the session's allowed-tools list. These per-plugin servers are merged with the always-on `qt` server (screenshot, widget tree, click, type, logs).
4. **Teardown.** The session plugin directory is temporary and rebuilt on the next agent construction. Toggling a plugin in settings therefore takes effect on the next agent session, not mid-conversation.

## Complete Example

A plugin contributing both a skill prompt and one tool. The prompt-only pattern matches the built-in `panel_design` plugin (`lightfall.plugins.agents.panel_design`); the tool pattern matches `device_tools`.

```python
"""Ring-status agent plugin."""

from __future__ import annotations

from typing import Any

from lightfall.plugins.agent_plugin import AgentPlugin
from lightfall.plugins.agents._mcp_helpers import mcp_result


class RingStatusAgent(AgentPlugin):
    """Gives Claude storage-ring context and a current-reading tool."""

    @property
    def name(self) -> str:
        return "ring_status"

    @property
    def display_name(self) -> str:
        return "Ring Status"

    @property
    def description(self) -> str:
        return "Storage-ring status context and a beam-current tool"

    @property
    def category(self) -> str:
        return "operations"

    @property
    def priority(self) -> int:
        return 50

    def get_system_prompt(self) -> str:
        return (
            "## Storage Ring Status\n\n"
            "When the user asks about beam availability, check the ring "
            "current with the ring_current tool before answering. "
            "Top-off mode holds the current near 500 mA; values near zero "
            "mean no beam."
        )

    def create_tools(self) -> list[Any]:
        try:
            from claude_agent_sdk import tool
        except ImportError:
            return []

        @tool(
            name="ring_current",
            description="Read the current storage-ring beam current in mA",
            input_schema={"type": "object", "properties": {}},
        )
        async def ring_current(args: dict) -> dict[str, Any]:
            value = read_ring_current_somehow()  # your data source
            return mcp_result({"current_mA": value})

        return [ring_current]
```

With this plugin enabled, the agent's session contains a `skills/ring_status/SKILL.md` and the tool is callable as `mcp__ring_status__ring_current`.

## Registration

Add a manifest entry (built-in manifest during development, or your package's manifest for distribution — see [External Packages](../external-packages.md)):

```python
PluginEntry(
    type_name="agent",
    name="ring_status",
    import_path="my_beamline.agents.ring_status:RingStatusAgent",
),
```

## Built-in agent plugins

The built-in manifest registers agent plugins covering panel interaction (`lightfall_core_tools`), devices (`device_tools`), plans (`plan_tools`), the engine (`engine_tools`), the IPython console (`ipython_tools`), panel and plan authoring expertise (`panel_design`, `panel_builder`, `plan_design`), scan planning and alignment guidance (`scan_planning`, `alignment`), adaptive experiments (`autonomous_experiment`), and the current ESAF (`current_esaf`). Reading their sources under `src/lightfall/plugins/agents/` is the fastest way to learn the patterns.
