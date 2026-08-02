---
name: panel-builder
description: Tools for creating and managing user plugins
---

## Plugin Building Tools

You have access to tools for creating user plugins in Lightfall.

### Plugin Kinds

Two kinds of user plugins are supported. The kind is inferred from what your
code defines and registers:

- **Panel plugin** — a `BasePanel` subclass with a `panel_metadata` class
  attribute, self-registered at module scope with
  `PanelRegistry.get_instance().register(MyPanel, replace=True)`. This is
  the canonical user-plugin pattern; see the `panel_design` skill for the
  full `BasePanel` API.
- **Agent plugin** — an `ToolPlugin` subclass that extends the embedded
  Claude agent (skill prompts + MCP tools). Auto-registered via
  `PluginType.__init_subclass__` on module load.

### Creating a Plugin

Use `lightfall_create_user_plugin` to write a plugin file to ~/lightfall/plugins/.
The plugin is validated (syntax + exec + at least one panel registration
or concrete ToolPlugin subclass), written to disk, and loaded immediately.

Example workflow:
1. User asks for a panel with specific functionality.
2. You generate a `BasePanel` subclass with a `panel_metadata` and a
   trailing `PanelRegistry.get_instance().register(MyPanel, replace=True)`.
3. You call `lightfall_create_user_plugin` with that source.
4. The plugin is validated, written to disk, and loaded.
5. User can open the panel from View > User > [Panel Name].

### Quick Prototyping

For rapid prototyping, use `lightfall_create_temp_plugin` to create a temporary
plugin that will be lost on application restart. This is useful for testing
ideas before committing to a persistent plugin.

### Plugin Management

- `lightfall_list_user_plugins`: See all loaded user plugins and their status
- `lightfall_reload_plugin`: Force reload after external edits
- `lightfall_unload_plugin`: Remove a plugin from the registry
