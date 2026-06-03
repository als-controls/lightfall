# SDK-Native Skills + Per-Plugin MCP Servers + Unified `AgentPlugin`

## Problem

Lightfall's embedded Claude integration uses `claude-agent-sdk >= 0.1.30` correctly for in-process MCP tools, but its **skill system is homegrown and predates the SDK's native plugin/skill mechanism**. Concretely:

1. **Skills are eagerly injected into the system prompt.** `SkillRegistry.get_aggregated_system_prompt()` (`lightfall/ui/panels/claude/skill_registry.py:176-216`) concatenates every enabled skill's `get_system_prompt()` text into one blob, which `claude_panel.py:651-669` appends to `additional_system_prompt`. Every enabled skill burns context unconditionally; there is no lazy invocation. A docstring on `SkillRegistry.get_skill_reminder()` (`skill_registry.py:320`) explicitly notes the divergence: *"Unlike Claude Code's Skill tool pattern, these skills are pre-loaded — their prompts and tools are already in the context."*

2. **All plugin tools are bundled into one SDK MCP server.** `lightfall/claude/agent.py:252-260` calls `create_sdk_mcp_server(name="additional", …)` once with the union of every enabled plugin's tools. Tool namespaces all collapse to `mcp__additional__*`; there is no per-plugin server boundary. The SDK supports per-server granularity, but lightfall is not using it.

3. **Two near-identical Python class hierarchies for "agent extensions."** `MCPToolPlugin` (`lightfall/plugins/mcp_tool.py`) holds tools. `SkillPlugin` (`lightfall/plugins/skill_plugin.py`) extends `MCPToolPlugin` to additionally hold a prompt. The settings UI (`tool_settings.py`) treats them uniformly via a "Type" column, but they have separate registries (`MCPToolRegistry`, `SkillRegistry`) and the registration path branches twice in `loader.py`.

4. **The user-plugin loader uses a registration-tracking patching trick.** `RegistrationTracker` in `user_plugins.py:51-160` monkey-patches `PanelRegistry.register`, `SkillRegistry.register_plugin`, and `MCPToolRegistry.register_plugin` during `exec()` of a user file, so it can record what was registered. User files must explicitly call `Registry.get_instance().register(MyClass)`.

The cost: skills eat context they don't need to, tool namespaces lack structure, and the plugin taxonomy has redundant types whose contracts overlap. Migrating to SDK-native skills is also a hard prerequisite for **Spec B** (Blackfly observer refactor + Blackfly skill), which should not be authored on the soon-to-be-deprecated `SkillPlugin` class.

## Goals

- **Replace homegrown skills with SDK-native skills.** Skill prompts move out of the eagerly-injected system prompt and into per-session synthesized `SKILL.md` files. The SDK's deferred `Skill` tool surfaces them lazily; the model invokes a skill by name only when its `description` makes it relevant.
- **Split the bundled `mcp__additional__*` SDK server into per-plugin servers.** One `create_sdk_mcp_server(name=plugin.name)` per plugin with non-empty tools; namespaces become `mcp__device_tools__*`, `mcp__panel_builder__*`, etc.
- **Unify `SkillPlugin` and `MCPToolPlugin` into one `AgentPlugin` type.** One Python class. One settings toggle controls the bundled prompt + tools.
- **Generalize the user-plugin loader** so any `PluginType` subclass defined under `~/lightfall/plugins/` is auto-enqueued via `__init_subclass__`. Eliminate the `RegistrationTracker` patching and the explicit `Registry.register()` calls in user files.
- **Preserve the manifest-as-source-of-truth contract.** Each beamline package contributes agents through `PluginEntry` declarations the same way it contributes panels/themes/controllers — the new `agent` branch in the loader is structurally identical to the existing branches.

## Non-goals

- Tool-taxonomy reorganization beyond the literal class merges (no merging/splitting tools across plugins).
- Per-tool enable/disable granularity (one toggle per `AgentPlugin`).
- Migration of `controller_plugin`, `panel_plugin`, `theme_plugin`, `engine_plugin`, etc. to SDK-native equivalents — those have no SDK analogue. Only skills + tools migrate.
- Bundle-coupling between unrelated plugins in the settings UI (each `AgentPlugin` is independently toggleable; only its own internal prompt + tools toggle together).
- The Blackfly observer refactor, Blackfly skill, or any other beamline-specific work — those are Spec B.

## High-level architecture

```
[Package install]                  [Lucid startup]                     [Agent session start]

lightfall/plugins/agents/           PluginManifest entries                 ClaudeAgentOptions(
  alignment.py                                                           plugins=[
    class AlignmentAgent(       ─── loader.py                              {"type":"local",
        AgentPlugin):                elif type_name=="agent":               "path": <synthesized
                                       resolve import_path                   tmp dir with
lightfall/plugins/agents/                  instantiate                           enabled skills>}
  panel_builder.py                     register w/ AgentRegistry         ],
    class PanelBuilderAgent(    ───→                                    mcp_servers={
        AgentPlugin):                                                     "device_tools":
                                                                            create_sdk_mcp_server(...),
lightfall_endstation_7011/                                                    "plan_tools": ...,
  agents/                                                                 "panel_builder": ...
    blackfly.py                                                         },
                                                                        allowed_tools=[
~/lightfall/plugins/                __init_subclass__ on PluginType           "mcp__device_tools__*",
  my_plugin.py                  ─── enqueue user contributions             ...per plugin...
                                    via UserPluginService               ],
                                                                        system_prompt=<base only;
                                                                          no skill content>,
                                                                      )
```

Three shifts from today:

- **`system_prompt` becomes static and small.** The skill-aggregation block in `claude_panel.py:651-669` is removed. Skills self-document via `SKILL.md` frontmatter; the SDK `Skill` tool loads bodies on demand.
- **`mcp_servers` becomes a dict of per-plugin servers**, keyed by `plugin.name`. The pre-existing `qt` server (Qt UI tools) stays separate — it's not a plugin contribution.
- **A per-session synthesized plugin directory** is the sole bridge between lightfall's plugin registry and the SDK's `plugins=` parameter. Built from `AgentRegistry` at agent construction; rebuilt on settings change + reconnect; cleaned up at session end.

## Design

### `AgentPlugin` contract

Single unified type for plugins that extend the embedded Claude agent. Replaces both `SkillPlugin` and `MCPToolPlugin`.

```python
# lightfall/plugins/agent_plugin.py
class AgentPlugin(PluginType):
    """Extends the embedded Claude agent with an optional skill prompt and/or
    a bag of MCP tools. One plugin = one settings toggle. When enabled, contributes:
      - a SKILL.md (if get_system_prompt() is non-empty), materialized into
        the per-session SDK plugin dir;
      - an in-process MCP server (if create_tools() is non-empty), registered
        as mcp_servers[plugin.name] with namespace mcp__<plugin.name>__*.
    """

    type_name: ClassVar[str] = "agent"
    is_singleton: ClassVar[bool] = True

    @property
    @abstractmethod
    def name(self) -> str: ...                            # ≤64 chars, lowercase + hyphens/underscores; see Open questions

    @property
    @abstractmethod
    def description(self) -> str: ...                     # SKILL.md frontmatter description (≤1024 chars per SDK)

    @property
    def display_name(self) -> str: return self.name.replace("_", " ").title()

    @property
    def category(self) -> str: return "general"          # settings-UI grouping

    @property
    def enabled_by_default(self) -> bool: return True

    @property
    def priority(self) -> int: return 100

    def get_system_prompt(self) -> str:
        """SKILL.md body. Empty string = this plugin does not contribute a skill."""
        return ""

    def create_tools(self) -> list:
        """List of @tool-decorated callables. Empty = this plugin does not contribute tools."""
        return []

    def get_references_dir(self) -> Path | None:
        """Optional package directory containing supplementary docs.

        Files are copied to <tmp_plugin_dir>/skills/<name>/references/ at
        session start, where the SDK Skill tool loads them lazily on demand.
        Replaces the current get_documentation_path() / ncs_get_skill_docs pattern.
        """
        return None
```

Validation occurs at registration time (in the loader branch): `name` matches `^[a-z][a-z0-9_-]{0,63}$`; if not, registration fails with a clear error. `description` is truncated to 1024 chars at synthesis time with a logged warning.

### Loader registration

One new branch in `lightfall/plugins/loader.py`, structurally identical to the existing `panel`/`theme`/`controller` branches:

```python
elif plugin_info.type_name == "agent":
    from lightfall.ui.panels.claude.agent_registry import AgentRegistry
    AgentRegistry.get_instance().register(plugin_info.instance)
```

`AgentRegistry` is a slimmed singleton replacing both `SkillRegistry` and `MCPToolRegistry`. Responsibilities:

- Hold registered `AgentPlugin` instances.
- Expose them to the settings UI for enable/disable.
- At agent-construction time, produce the inputs needed for SDK assembly: `enabled_plugins()` returns the enabled `AgentPlugin` instances sorted by priority.

The preference key stays `enabled_tool_plugins` (name retained for backward compat with existing user settings; semantics identical — set of enabled plugin names).

Manifest entries shift from `type_name="skill"` / `type_name="mcp_tool"` to a single `type_name="agent"`:

```python
PluginEntry(
    type_name="agent",
    name="panel_builder",
    import_path="lightfall.plugins.agents.panel_builder:PanelBuilderAgentPlugin",
)
```

### Session-time SDK assembly

Lives in `lightfall/claude/agent.py` (`QtClaudeAgent.__init__`) and the path that constructs it (`claude_panel.py`).

**1. Synthesize a per-session plugin directory:**

```python
session_plugin_dir = Path(tempfile.mkdtemp(prefix="lightfall_claude_"))
(session_plugin_dir / ".claude-plugin").mkdir()
(session_plugin_dir / ".claude-plugin" / "plugin.json").write_text(
    json.dumps({"name": "lightfall-session", "version": "0.0.0"})
)

for plugin in agent_registry.enabled_plugins():
    _materialize_skill(plugin, session_plugin_dir)


def _materialize_skill(plugin: AgentPlugin, plugin_dir: Path) -> None:
    body = plugin.get_system_prompt().strip()
    if not body:
        return                                            # tools-only plugin: skip skill creation
    skill_dir = plugin_dir / "skills" / plugin.name
    skill_dir.mkdir(parents=True)
    description = plugin.description[:1024]               # SDK frontmatter limit
    if len(plugin.description) > 1024:
        logger.warning("agent '{}': description truncated to 1024 chars", plugin.name)
    frontmatter = f"---\nname: {plugin.name}\ndescription: {description}\n---\n\n"
    (skill_dir / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")
    if (refs := plugin.get_references_dir()) is not None:
        shutil.copytree(refs, skill_dir / "references")
```

**2. Per-plugin MCP server assembly:**

```python
mcp_servers: dict[str, Any] = {"qt": qt_tools_server}    # always-on Qt UI tools (unchanged)
allowed_tools: list[str] = ["mcp__qt__screenshot", ...]  # Qt allowlist (unchanged)

for plugin in agent_registry.enabled_plugins():
    tools = plugin.create_tools()
    if not tools:
        continue                                          # skill-only plugin: skip server
    server = create_sdk_mcp_server(name=plugin.name, version="1.0.0", tools=tools)
    mcp_servers[plugin.name] = server
    for tool in tools:
        tool_name = getattr(tool, "name", None) or tool.__name__
        allowed_tools.append(f"mcp__{plugin.name}__{tool_name}")
```

**3. `ClaudeAgentOptions`:**

```python
# claude_panel.py's prompt builder still produces additional_system_prompt
# containing the lightfall context block (panel descriptions, RunEngine notes,
# tool selection guidelines, etc.). The change is only that the skill
# aggregation block — claude_panel.py:651-669 today — is removed; the rest
# of the prompt builder is unchanged.
options = ClaudeAgentOptions(
    plugins=[{"type": "local", "path": str(session_plugin_dir)}],
    mcp_servers=mcp_servers,
    allowed_tools=allowed_tools,
    system_prompt=f"{QT_SYSTEM_PROMPT}\n\n{additional_system_prompt}",  # no skill content baked in
    permission_mode=permission_mode,
    max_turns=max_turns,
    can_use_tool=...,
    hooks=...,
)
```

**Lifecycle:**
- *Session start*: synthesize plugin dir, build options, connect SDK client.
- *Settings UI changes mid-session*: tear down worker, rebuild plugin dir from new enabled set, reconnect on next query. The SDK reads `plugins=` once at client construction, so a reconnect is required for changes to apply.
- *Session end* (`QtClaudeAgent.stop()` or app exit): `shutil.rmtree(session_plugin_dir, ignore_errors=True)`.

### Generalized user-plugin path

The `RegistrationTracker` patching dance in `user_plugins.py:51-160` is replaced by `__init_subclass__` on `PluginType`:

```python
# lightfall/plugins/types.py
class PluginType(ABC):
    type_name: ClassVar[str]
    is_singleton: ClassVar[bool] = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__module__ == "__main__" or inspect.isabstract(cls):
            return
        try:
            module_file = Path(inspect.getfile(cls)).resolve()
        except (TypeError, OSError):
            return
        if _is_under_user_plugin_dir(module_file):
            UserPluginService.get_instance().enqueue(cls, module_file)
```

`_is_under_user_plugin_dir(p)` returns True iff `p` is under `~/lightfall/plugins/` or the per-session prototype temp dir. Strict path comparison; nothing else triggers auto-registration. Built-in plugins (under site-packages or development source trees outside the user plugin dir) flow through the existing manifest-driven path; `__init_subclass__` is a no-op for them.

`UserPluginService` simplifies to:

```python
class UserPluginService(QObject):
    def enqueue(self, cls: type[PluginType], file_path: Path) -> None:
        """Called from PluginType.__init_subclass__. Routes the class to its
        type-specific registry via the PluginLoader._register_plugin machinery,
        and tracks (file_path, type_name, name) for unload."""
        ...

    def load_file(self, path: Path) -> PluginInfo:
        """Exec the file in an isolated namespace. __init_subclass__ fires for
        each PluginType subclass; enqueue() routes them. Returns a PluginInfo
        with the list of (type_name, name) registered."""
        ...

    def unload_file(self, path: Path) -> None:
        """Look up registered (type_name, name) pairs for this file and
        unregister from the appropriate registries."""
        ...
```

The `RegistrationTracker` class and the three `_patch_*_registry` methods are deleted.

User files become simpler — explicit `Registry.register()` calls are no longer needed:

```python
# ~/lightfall/plugins/my_panel.py
from lightfall.plugins.panel_plugin import PanelPlugin

class MyPanelPlugin(PanelPlugin):
    @property
    def name(self) -> str: return "my_panel"
    def get_panel_class(self): return MyPanelClass
# Subclassing alone triggers registration via __init_subclass__.
```

The `ncs_create_user_plugin` MCP tool keeps its current name and signature; the `kind` of plugin is determined by what the file's class subclasses (no `kind` parameter). Validation (syntax check, isolated-exec test, dangerous-import warnings) carries over from `panel_builder.py:89-122`. The `panel_builder` agent's prompt teaches Claude the available `lightfall.plugins.*` superclasses and a template per kind.

## Migration

### Built-in plugin map

Five existing skills + four tool plugins (`skill_docs` deleted) become 9 `AgentPlugin` instances:

| Today | New location | Tools? | Prompt? | Notes |
|---|---|:-:|:-:|---|
| `lightfall/plugins/skills/alignment.py` | `lightfall/plugins/agents/alignment.py` | — | ✓ | Pure-prompt |
| `lightfall/plugins/skills/plan_design.py` | `lightfall/plugins/agents/plan_design.py` | — | ✓ | Pure-prompt |
| `lightfall/plugins/skills/scan_planning.py` | `lightfall/plugins/agents/scan_planning.py` | — | ✓ | Pure-prompt |
| `lightfall/plugins/skills/panel_design.py` | `lightfall/plugins/agents/panel_design.py` | — | ✓ | Pure-prompt |
| `lightfall/plugins/skills/panel_builder.py` | `lightfall/plugins/agents/panel_builder.py` | ✓ | ✓ | Both — `ncs_create_user_plugin` and friends stay in the same class |
| `lightfall/plugins/tools/device_tools.py` | `lightfall/plugins/agents/device_tools.py` | ✓ | — | Pure-tools |
| `lightfall/plugins/tools/plan_tools.py` | `lightfall/plugins/agents/plan_tools.py` | ✓ | — | Pure-tools |
| `lightfall/plugins/tools/engine_tools.py` | `lightfall/plugins/agents/engine_tools.py` | ✓ | — | Pure-tools |
| `lightfall/plugins/tools/ipython_tools.py` | `lightfall/plugins/agents/ipython_tools.py` | ✓ | — | Pure-tools |
| `lightfall/plugins/tools/skill_docs_tool.py` | **deleted** | — | — | Replaced by SDK's deferred `Skill` tool + `references/` |

Each migrated class swaps its base from `SkillPlugin` / `MCPToolPlugin` to `AgentPlugin`. Property and method bodies carry over verbatim. `lightfall/plugins/skills/docs/<name>.md` files (where present) move to `lightfall/plugins/agents/<name>/references/<name>.md`, and the `AgentPlugin` overrides `get_references_dir()` to point at the directory.

### Files added / deleted / modified

**Added:**
- `lightfall/plugins/agent_plugin.py` — the unified `AgentPlugin` class
- `lightfall/ui/panels/claude/agent_registry.py` — slimmed singleton replacing `SkillRegistry` and `MCPToolRegistry`
- `lightfall/plugins/agents/` — package containing all 9 migrated classes

**Deleted:**
- `lightfall/plugins/skill_plugin.py`
- `lightfall/plugins/mcp_tool.py`
- `lightfall/ui/panels/claude/skill_registry.py`
- `lightfall/ui/panels/claude/tool_registry.py` (or absorbed into `agent_registry.py`)
- `lightfall/plugins/tools/skill_docs_tool.py`
- `lightfall/plugins/skills/` (after migrating contents)
- `lightfall/plugins/tools/` (helpers like `_mcp_helpers.py` move to `lightfall/plugins/agents/_mcp_helpers.py` or `lightfall/plugins/_mcp_helpers.py`)
- `RegistrationTracker` and the three `_patch_*_registry` methods in `user_plugins.py`

**Modified:**
- `lightfall/plugins/types.py` — adds `__init_subclass__` to `PluginType`
- `lightfall/plugins/loader.py` — `skill` + `mcp_tool` branches replaced by single `agent` branch
- `lightfall/plugins/builtin_manifest.py` — `type_name="skill"` / `"mcp_tool"` entries become `type_name="agent"`; `skill_docs` entry removed
- `lightfall/plugins/user_plugins.py` — drops `RegistrationTracker`; `UserPluginService` tracks via `__init_subclass__` callbacks
- `lightfall/claude/agent.py` — per-plugin MCP server assembly; `plugins=` parameter; removal of "additional" server bundle
- `lightfall/ui/panels/claude_panel.py` — removes the `SkillRegistry.get_aggregated_system_prompt()` block (lines 651-669); calls into `AgentRegistry` to drive synthesis
- `lightfall/ui/preferences/tool_settings.py` — drops "Type" column; reads from `AgentRegistry`

### Branch commit shape

Spec A lands on a long-lived feature branch (`feature/sdk-native-agents` or similar) over six focused commits, merged once after end-to-end validation. No parallel-paths code; old types get ripped out as soon as the new ones are functional.

1. **Add `AgentPlugin` + `AgentRegistry` + `__init_subclass__` infrastructure.** No migrations yet; the new types exist but nothing uses them.
2. **Migrate the 9 built-in plugins** to `lightfall/plugins/agents/`, all subclassing `AgentPlugin`. Update `builtin_manifest.py` to `type_name="agent"`. Old base classes still exist; nothing references them after this commit.
3. **Wire session-time SDK assembly to `AgentRegistry`.** Update `claude/agent.py` for per-plugin MCP servers + `plugins=` path. Remove the `additional_system_prompt` skill aggregation block in `claude_panel.py`. Delete `SkillPlugin`, `MCPToolPlugin`, `SkillRegistry`, old `MCPToolRegistry`, `skill_docs_tool.py`, `lightfall/plugins/skills/`, `lightfall/plugins/tools/`.
4. **Simplify `UserPluginService`** — remove `RegistrationTracker`, switch to `__init_subclass__` callbacks.
5. **Settings UI updates** — drop "Type" column; rename internal helpers as appropriate.
6. **Tests** — round out coverage (see Testing).

Estimated 1-2 weeks of work with daily commits; reviewer can step through commits in order.

### Breaking changes for users

- Any user plugin that subclasses `SkillPlugin` or `MCPToolPlugin` directly breaks. **Migration**: change the base to `AgentPlugin`; the `name`/`description`/`category`/etc. properties, `create_tools()`, `get_system_prompt()` carry over verbatim.
- Any user plugin that explicitly calls `Registry.get_instance().register(...)` keeps working but the call becomes redundant (`__init_subclass__` already registered the class). One-time runtime warning logged the first time it happens.
- The `ncs_get_skill_docs` and `ncs_list_skills` MCP tool calls disappear. The deferred SDK `Skill` tool surfaces the same information natively to Claude; humans browsing settings see plugin descriptions in the table.
- The `enabled_tool_plugins` preference key carries over unchanged (set semantics, plugin names unchanged).

## Settings UI

`tool_settings.py` (`ClaudeToolsSettingsPlugin`) becomes the agent-plugin settings panel. Concrete changes:

- "Type" column dropped (everything is `AgentPlugin`).
- Columns: **Plugin / Category / Description / [✓]**.
- Rows source from `AgentRegistry.get_plugins()`; sort by category, then `display_name`.
- Preference key unchanged (`enabled_tool_plugins`), default-from-`enabled_by_default` logic unchanged, change-detection (`has_changes()`) unchanged.
- Mid-session apply: triggers `QtClaudeAgent.stop()` + reconnect on next query, which rebuilds the synthesized plugin dir from the new enabled set.
- Optional cosmetic: `tool_settings.py` → `agent_settings.py`, `ClaudeToolsSettingsPlugin` → `ClaudeAgentsSettingsPlugin`, manifest entry name `claude_tools` → `claude_agents`. Defer if not worth the rename.

## Testing strategy

Five testable units; the branch commits map roughly 1:1 with these test additions.

**1. `AgentPlugin` contract + `AgentRegistry`** (pure-Python unit tests):
- Default `get_system_prompt()` returns `""`, default `create_tools()` returns `[]`.
- `name` / `description` are abstract; `display_name` / `category` / `priority` have sensible defaults.
- Registry: register/unregister, duplicate-name handling, enabled-set filtering by preference, enable_by_default fallback when no preference set, introspection data shape.

**2. Loader `agent` branch:**
- Valid manifest entry resolves `import_path`, instantiates, registers with `AgentRegistry`.
- Bad import_path → logged error, no registration.
- Class isn't an `AgentPlugin` subclass → load error.
- Abstract class → skipped (handled by `__init_subclass__`'s abstract guard).

**3. Session synthesis** (against a temp directory):
- `_materialize_skill` for a plugin with prompt → SKILL.md exists with correct YAML frontmatter (`name`, `description` ≤1024 chars), correct body.
- Plugin without prompt → no `skills/<name>/` dir created.
- Plugin with `get_references_dir()` returning a path → `references/` copied alongside SKILL.md.
- Description >1024 chars → truncated with logged warning.
- MCP server assembly: plugin with tools → entry in `mcp_servers` keyed by `plugin.name`, `mcp__<plugin.name>__<tool>` entries in `allowed_tools`.

**4. `__init_subclass__` user-plugin auto-enqueue:**
- Class defined in a file under `~/lightfall/plugins/` → enqueued.
- Class defined in a file under `site-packages/` → not enqueued.
- Class defined in `__main__` (REPL) → not enqueued.
- Abstract class → not enqueued.
- `UserPluginService` correctly tracks `(file, type_name, name)` for unload.
- Hot-reload: file change → unload + re-exec → new class auto-enqueues + registers.

**5. Migration regression / parity** (one snapshot test per migrated built-in):
- Migrated `BeamlineAlignmentAgentPlugin.get_system_prompt()` returns the same body as today's `BeamlineAlignmentSkill.get_system_prompt()` (string equality against a fixture).
- Migrated `PanelBuilderAgentPlugin.create_tools()` returns 5 tools with the same names as today's `PanelBuilderSkill.create_tools()` returns.
- Catches accidental content drift during the file moves in commit 2.

**Manual smoke test** (gate before merge):
1. Start lightfall, open Claude panel.
2. Settings → Claude Tools → verify all 9 agent plugins appear, defaults match.
3. Send a query exercising one tool from each per-plugin server (e.g., `ncs_list_devices`, `ncs_run_plan`, `ncs_create_user_plugin`) — verify each routes through its own namespace (`mcp__device_tools__*`, `mcp__plan_tools__*`, `mcp__panel_builder__*`).
4. Send a query that should trigger a skill (e.g., "help me align this motor") — verify the SDK's deferred `Skill` tool fires for `alignment` and the prompt body loads.
5. Disable `panel_builder` in settings, reconnect, verify panel-creation tools no longer in `allowed_tools`.
6. Drop a small user `AgentPlugin` (e.g., a one-tool plugin) into `~/lightfall/plugins/test_user_plugin.py` — verify it appears in settings and is callable.

## Out of scope

- **Spec B — Blackfly observer refactor + Blackfly skill.** Depends on Spec A's foundation. Moves `CameraBase` / `CameraImageView` from the standalone `blackfly_observer` repo into `lightfall.ui.widgets.observers`, the GVCP/GVSP transport stack into `lightfall_endstation_7011.observers.blackfly`, and adds a Blackfly `AgentPlugin` to the endstation manifest. Brainstormed but paused behind Spec A.
- **Tool-taxonomy reorganization.** Moving individual `@tool` functions across `AgentPlugin` boundaries (e.g., consolidating user-plugin-management tools out of `panel_builder`) is a separate followup.
- **Per-tool enable/disable** at the settings UI level. One toggle per plugin.

## Open questions / followups

- **Plugin name format: underscores vs. hyphens.** The SDK's docs state SKILL.md frontmatter `name` is "lowercase/hyphens only" (≤64 chars), but lightfall's existing plugin names use underscores (`panel_builder`, `plan_design`, `scan_planning`, `device_tools`, …). To resolve before commit 1: either (a) verify the SDK actually accepts underscores in `name` (in which case no change), (b) keep `plugin.name` as-is (underscores) but transform underscores → hyphens at SKILL.md materialization time so the frontmatter `name` is SDK-compliant while internal identifiers / preference keys / Python class lookup unchanged, or (c) rename the existing plugins to use hyphens. (b) is least disruptive and keeps `plugin.name` as the canonical lightfall identifier; recommend (b) absent SDK docs to the contrary. Same question applies to `mcp_servers` dict keys and `mcp__<server>__<tool>` namespace, but those are SDK-internal and have always used underscores in lightfall (`mcp__additional__*` today), so probably safe.
- **`get_references_dir()` packaging.** Built-in references should ship as importable package data. The exact resolution (`importlib.resources.files(package_name)`) should be confirmed against the wheel-build setup during commit 1.
- **`tool_settings.py` rename.** Cosmetic; defer if not worth the rename. Decision can land in commit 5.
- **Endstation packages contributing references.** Spec B's Blackfly skill will exercise `get_references_dir()` from outside `lightfall` for the first time; commit 6 (tests) should include a fixture that mimics this.
- **Backward-compat shim?** Currently this design has no `SkillPlugin = AgentPlugin` / `MCPToolPlugin = AgentPlugin` aliases. If existing user plugins in the wild prove painful to update, a one-release deprecation alias could be added — defer until evidence.
