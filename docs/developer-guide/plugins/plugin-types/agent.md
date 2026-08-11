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

## Inter-agent messaging: the bus

Every `QtClaudeAgent` session (regardless of which `AgentSpec` is driving it) gets an
always-on `bus` SDK MCP server, separate from the per-plugin servers described above:

- **`mcp__bus__send_message`** — `{"to": "<agent name>", "message": "<text>"}`. Delivers
  to another agent registered on the shared `AgentBus` (`lightfall.agents.bus.AgentBus`),
  or returns an error result (with the current roster in the detail) if `to` isn't
  registered.
- **`mcp__bus__list_agents`** — no arguments. Returns each registered agent's `name`,
  `description`, and current `busy` state, so an agent can check availability before
  sending.

Registration happens per session — a session's `bus_name` is whatever name it was
registered under (duplicate names get a `#2`, `#3`, ... suffix from `AgentBus.register`).
The tools read the sender's name live at call time via a callable, not a captured
string, since a session's bus name can be reassigned after registration.

### Delivery policy: `lightfall.on_message`

Each `AgentSpec` (parsed from an agent definition's `lightfall:` frontmatter block, see
`lightfall/agents/spec.py`) declares how *incoming* bus messages are handled by that
agent's `ClaudeSessionEndpoint` (`lightfall.claude.bus_endpoint.ClaudeSessionEndpoint`):

```yaml
lightfall:
  on_message: queue   # or: auto
```

- **`"queue"`** (default) — every incoming message is queued and the UI is notified
  (`on_queued`) so a pending-message banner appears in the Claude panel. The user
  reviews and explicitly flushes it (the "Respond" action), which calls
  `flush_pending()` and submits the queued prompts as a turn.
- **`"auto"`** — if the session is idle (`is_busy()` is false), the message is
  submitted immediately as a turn. If the session is busy (or `submit` refuses,
  e.g. a race where busy-ness flips between the check and the call), the message is
  queued instead of dropped, and gets flushed the next time the caller decides the
  session is free (e.g. on `query_completed` for widget wiring).

The Claude panel also exposes a **"Auto-respond to agent messages"** checkable toggle
in its tune/settings popup menu (`ClaudeAssistantWidget._on_toggle_auto_respond` in
`lightfall/claude/widget.py`), which calls `bus_endpoint.set_policy("auto"/"queue")`
directly — this overrides the spec's declared default for the running session without
editing the agent file.

Every delivered or queued message is rendered into the session as:

```
[Message from agent '<sender>']
<message text>
```

via `format_bus_prompt()` — this is the exact text `submit` receives, whether
delivered immediately (`auto`) or via `flush_pending()` (both policies).

## Skill drafts & approval

Agents can propose new skills or revisions to existing ones via the **`draft_skill`** MCP tool
(`mcp__skills__draft_skill`). This tool takes three parameters:

- **`name`** — skill name (lowercase alphanumeric, hyphens, underscores; 1-64 chars).
- **`description`** — one-line human-readable description (must not contain newlines; ≤1024 chars).
- **`body`** — skill body content (markdown).

Drafts are always inert — a human must review and approve them before they take effect.
The tool returns the filesystem path where the draft was saved and indicates whether it
is a new draft or a revision of an existing skill.

### File layout and collision detection

Drafts are stored in `~/lightfall/skills/_drafts/` with provenance frontmatter:

- **New draft** — if `name` does not collide with an active skill, the draft is written to
  `_drafts/<name>/SKILL.md`.
- **Revision** — if `name` collides with an active skill, a `.proposed` revision is written
  to `_drafts/<name>/SKILL.md.proposed`. This signals that the draft is intended to replace
  or update an existing skill, not create a parallel one.

Redrafting an existing draft (same name, same collision state) overwrites the previous
version. When a revision is drafted, any stale non-revision draft for that name is deleted.

### Provenance frontmatter

Every draft file includes metadata in YAML frontmatter under a `lightfall-draft:` key:

```yaml
---
name: my-skill
description: "What this skill does"
lightfall-draft:
  author: Alice
  created: 2026-08-02
  session_id: abc123...
---
```

- **`author`** — name of the session's Claude agent (e.g., the bus name).
- **`created`** — ISO date the draft was created.
- **`session_id`** — optional session identifier for traceability; omitted if not available.

### Inertness guarantee

Drafts in `_drafts/` are **never injected into agent prompts** during session assembly.
The session assembler materializes only skills from the active `~/lightfall/skills/<name>/`
directory. This means agents cannot accidentally ship incomplete or unapproved drafts, and
drafts are completely safe to experiment with.

### Manual approval procedure

To promote a draft into active use, a human must:

1. **New skill** — move the draft directory out of staging:
   ```bash
   mv ~/lightfall/skills/_drafts/<name>/ ~/lightfall/skills/<name>/
   ```
   The next agent session will automatically load the active skill.

2. **Revision of existing skill** — diff the `.proposed` file against the active one,
   review the changes, then:
   ```bash
   # Replace the active skill with the revision
   mv ~/lightfall/skills/_drafts/<name>/SKILL.md.proposed ~/lightfall/skills/<name>/SKILL.md
   # Clean up the draft directory
   rm -rf ~/lightfall/skills/_drafts/<name>/
   ```
   The next agent session will use the updated skill.

3. **Rejection** — delete the draft if it should not be promoted:
   ```bash
   rm -rf ~/lightfall/skills/_drafts/<name>/
   ```

The frontmatter (author, created, session_id) is retained in the active skill when promoted.
This preserves provenance for auditing and reference.

## Phase 4: Tabbed multi-agent UI & editor panel

The Claude panel UI in Phase 4 introduces:

1. **Tabbed multi-agent sessions** — Each agent gets its own tab, independent conversation state, and unread badges.
2. **Inline message cards** — Inter-agent messages appear as Accept/Dismiss cards instead of a banner.
3. **Agent openability control** — Per-agent frontmatter field to prevent certain agents (e.g. monitor/observer) from being user-opened.
4. **Editor panel** — A combined Agents & Skills management UI with draft approval workflows, replacing manual filesystem operations.

### Tabbed panel and agent picker

The Claude panel hosts a `QTabWidget` with a "+" button in the corner. On first launch, a single uncloseable **lightfall** tab appears, hosting the main assistant session. Users can open additional agent tabs via the picker menu.

The **picker button** ("+" button in the panel corner) opens a context menu listing available agents:

- Agents not currently open and marked `openable: true` in their spec
- Displayed with scope suffix (e.g. "Observer (core)") and description tooltip
- Sorted by scope (core agents first) then name
- Observer and other system agents are not openable (`openable: false` in their spec frontmatter)

Agent specifications:

```yaml
---
name: my_custom_agent
description: Describes what this agent does
lightfall:
  openable: false  # Prevent user from opening this agent in a tab
---
<agent prompt body>
```

The `openable` field (default: `true`) controls whether a user can open this agent as a tab.
System agents like **observer** set `openable: false` to remain background-only (forwarding monitor summaries to the lightfall tab).

**Tab behavior:**

- All tabs are closable except the uncloseable **lightfall** tab
- Tab text shows agent name; when unread agent messages are pending, it becomes `"<name> (N)"` (badge)
- Closing a tab terminates that agent's session (unregisters from the agent bus)
- `submit_external_prompt()` and `discuss_observation()` routing always targets the **lightfall tab**, activating it if needed

### Inline Accept/Dismiss message cards

When an agent sends a message to another agent (via `send_message` MCP tool), the message routing policy (defined in the receiving agent's spec) determines how it is rendered:

**Queue policy** (`lightfall: {on_message: queue}`, the default):

```yaml
lightfall:
  on_message: queue
```

Messages appear as interactive cards in the receiving agent's session:

```
⚡ from observer · 14:32
Severity info: Ring current stable at 495 mA. Ready for new topups.
[Accept] [Dismiss]
```

- **Accept** — submits the message as a turn in the conversation (Claude sees and responds to it)
- **Dismiss** — greys out the card and removes it from the pending queue without submitting
- A badge shows the unread count on the receiving agent's tab when it is not focused
- On agent bus congestion (session busy when Accept is clicked), a transient "agent busy — try again" note appears; the card remains

**Auto policy** (`lightfall: {on_message: auto}`):

```yaml
lightfall:
  on_message: auto
```

Messages are submitted immediately if the session is idle; if the session is busy, they queue and flush when idle. Cards appear with "auto-accepted" label instead of buttons:

```
⚡ from observer · 14:32 (auto-accepted)
Severity info: Ring current stable at 495 mA. Ready for new topups.
```

Both policies are runtime-toggleable via the tune-popup menu: **"Auto-accept agent messages"** checkbox overrides the spec's declared policy for the running session without editing the agent file.

### Agents & Skills editor panel

The **Agents & Skills** editor panel (sidebar, left area, id `lightfall.panels.agent_editor`) provides:

#### Agents tab

Lists all agent definitions grouped by scope (User, Beamline, Core):

- **Read-only rows** (built-in/beamline agents):
  - Description, scope, source-path link
  - **Copy to user scope** button — creates an editable shadow copy in the user agent directory
  - (If the agent is already shadowed:) **Reset to default** button — deletes the user shadow and restores the original

- **Editable rows** (user-scope agents):
  - Form editor: description (text), model (dropdown: blank/opus/sonnet/haiku), reasoning effort (dropdown: blank/low/medium/high), memory/subagent/openable toggles, on_message policy (queue/auto), available tools and skills (checkable lists)
  - **Prompt editor** — large text area with a static hint showing available template variables (`{{beamline}}`, `{{user}}`, `{{endstation}}`)
  - **Save** button — serializes frontmatter + body, validates by round-tripping through `parse_agent_file()`, writes the file; errors shown inline
  - **Export…** button — file dialog to copy the agent definition to another location

- **Error rows** (grayed, unparseable agent files):
  - Error message, scope, source path

- **New agent** button — prompts for a name, creates a template user-scope agent file

#### Skills tab

Lists active skills (from `resolve_skills()`) grouped by scope, plus a **Drafts** section when pending skill drafts exist:

- **Drafts section** (appears only when `list_drafts()` is non-empty):
  - Tabbed view: active skill vs. proposed draft (for revisions) or full draft text (for new skills)
  - Unified diff view (collapsible, generated via `difflib`) for revisions
  - **Approve** button — calls `approve_draft(name)` (moves new draft to active, or replaces active with proposed), increments Skills-tab badge counter, refreshes lists
  - **Reject** button (confirm dialog) — calls `reject_draft(name)`, removes draft, refreshes
  - **Edit then approve** — opens the draft in an inline editor, saves changes back to the draft file, then approves

- **Active skills section**:
  - Read-only display grouped by scope
  - Revisions are shown as side-by-side diffs: active (left) vs. draft proposal (right), highlighting changes

- **Skills-tab badge** — the tab text shows `"Skills (N)"` when N > 0 drafts are pending

The panel watches the drafts directory (`QFileSystemWatcher` on `drafts_dir()`). New or changed drafts automatically refresh the UI and update the badge.

### Manual approval procedure (fallback)

For users who prefer command-line workflows or if the editor panel is unavailable, manual promotion remains supported:

To promote a draft into active use:

1. **New skill** — move the draft directory out of staging:
   ```bash
   mv ~/lightfall/skills/_drafts/<name>/ ~/lightfall/skills/<name>/
   ```
   The next agent session will automatically load the active skill.

2. **Revision of existing skill** — diff the `.proposed` file against the active one,
   review the changes, then:
   ```bash
   # Replace the active skill with the revision
   mv ~/lightfall/skills/_drafts/<name>/SKILL.md.proposed ~/lightfall/skills/<name>/SKILL.md
   # Clean up the draft directory
   rm -rf ~/lightfall/skills/_drafts/<name>/
   ```
   The next agent session will use the updated skill.

3. **Rejection** — delete the draft:
   ```bash
   rm -rf ~/lightfall/skills/_drafts/<name>/
   ```

The editor panel is the primary path for approval workflows; this manual procedure is a fallback.

## Observer forwarding: proactive monitor summaries

The always-on `MonitorService` (`lightfall.monitor.service.MonitorService`) batches
observations from monitor feeds, periodically asks the `MonitorAdvisor` for a plain-text
summary (`_flush_advisor` / `_on_advisor_reply`), and — subject to a severity gate —
forwards that summary onto the bus as agent `"observer"` addressed to `"lightfall"`:

```
Proactive monitor summary (<severity>): <advisor reply>
```

### The severity gate is deterministic code, not the LLM

`_forward_to_agent` looks up the **`observer`** agent's `AgentSpec.forward_min_severity`
(from that agent file's `lightfall.forward_min_severity: info|warn|critical`
frontmatter key; defaults to `"warn"` if unset or the spec can't be resolved) and
compares it against the batch's max severity with `severity_at_least()`. Only if the
batch clears the floor does the summary go out over `AgentBus.send`. If no `"lightfall"`
endpoint is registered, the send fails and is degraded to a log line — it never raises
into the monitor's event loop.

**This gate is plain Python, evaluated before any bus/LLM call is made — the advisor
LLM only ever produces the summary text, and it never decides whether that summary is
allowed to reach the assistant.** The same invariant holds for the toast/panel path:
`_on_observation` raises a toast for `warn`/`critical` observations independent of, and
before, any advisor/forwarding logic runs. No amount of prompt content can widen or
bypass either gate.

### Per-feed preferences

Three preference keys (`lightfall.ui.preferences.monitor_settings`), one dict entry per
feed name, control what advisor batching sees *before* the forwarding gate above ever
runs:

| Pref key | Shape | Purpose |
|----------|-------|---------|
| `disabled_monitor_feeds` | `list[str]` | Feed names excluded from monitoring entirely |
| `monitor_feed_intervals` | `dict[str, int]` | Per-feed polling interval override (seconds) |
| `monitor_feed_advisor_severity` | `dict[str, str]` | Per-feed floor (`info`/`warn`/`critical`); an observation below its feed's floor never enters `_advisor_batch`, so it can't even contribute to a summary. Feeds absent from the dict default to `"info"` (nothing filtered). |

These feed-level floors and the `observer` spec's `forward_min_severity` floor are two
independent gates in series: a feed can contribute low-severity noise to the advisor's
context while the *forwarded summary* is still held back until the batch's overall
severity clears the observer's floor.

## Built-in agent plugins

The built-in manifest registers agent plugins covering panel interaction (`lightfall_core_tools`), devices (`device_tools`), plans (`plan_tools`), the engine (`engine_tools`), the IPython console (`ipython_tools`), panel and plan authoring expertise (`panel_design`, `panel_builder`, `plan_design`), scan planning and alignment guidance (`scan_planning`, `alignment`), adaptive experiments (`autonomous_experiment`), and the current ESAF (`current_esaf`). Reading their sources under `src/lightfall/plugins/agents/` is the fastest way to learn the patterns.
