# Plugin Types

Base classes for extending Lightfall. Every plugin implements one of the
plugin-type contracts below; plugins are declared in a manifest and loaded
by `PluginLoader` at startup.

## Import paths

The most common classes are re-exported from `lightfall.plugins`:

```python
from lightfall.plugins import (
    PluginType, PluginManifest, PluginEntry,
    PluginInfo, PluginRegistry, PluginLoader,
    AgentPlugin, ControllerPlugin, PanelPlugin, PlanPlugin, SettingsPlugin,
    PluginStatus, PluginError,
)
```

`EnginePlugin`, `StatusBarPlugin`, and `ThemePlugin` are not re-exported;
import them from their modules:

```python
from lightfall.plugins.engine_plugin import EnginePlugin
from lightfall.plugins.statusbar_plugin import StatusBarPlugin, StatusBarPluginMetadata
from lightfall.plugins.theme_plugin import ThemePlugin, ThemeDefinition
```

## Registration

### Manifests and entry points

Plugins are declared in a `PluginManifest` — a module-level object in your
package that lists `PluginEntry` items. An entry point in the
`lightfall.plugins` group points at the manifest:

```python
# my_beamline/manifest.py
from lightfall.plugins import PluginManifest, PluginEntry

manifest = PluginManifest(
    name="my-beamline-plugins",
    version="1.0.0",
    plugins=[
        PluginEntry("panel", "my_panel", "my_beamline.plugins:MyPanelPlugin",
                    preload=True),
        PluginEntry("agent", "my_tools", "my_beamline.plugins:MyAgentPlugin"),
    ],
)
```

```toml
# pyproject.toml
[project.entry-points."lightfall.plugins"]
my_beamline = "my_beamline.manifest:manifest"
```

Because the entry point references a manifest *module*, plugins can be
added, removed, or re-pointed by editing the manifest and restarting —
no package reinstall required.

`PluginEntry` fields:

| Field | Type | Description |
|-------|------|-------------|
| `type_name` | `str` | Plugin type (see table below). |
| `name` | `str` | Unique name within the type. `unique_id` is `"{type_name}:{name}"`. |
| `import_path` | `str` | `"module.path:ClassName"` (the colon is required). |
| `metadata` | `dict` | Optional plugin-specific metadata. |
| `preload` | `bool` | Load synchronously before the main window is created. Use for plugins that must apply before any UI appears (themes, appearance settings) and for panel plugins. |

### Plugin types loaded at startup

The application registers these type names with the loader
(`lightfall.main._setup_plugins`); manifest entries with any other
`type_name` are skipped with a warning:

| `type_name` | Base class | Provides |
|-------------|-----------|----------|
| `theme` | `ThemePlugin` | Color scheme + optional CSS overrides |
| `settings` | `SettingsPlugin` | A page in the Preferences dialog |
| `engine` | `EnginePlugin` | An execution engine (RunEngine wrapper) |
| `agent` | `AgentPlugin` | Skill prompt and/or MCP tools for the embedded Claude agent |
| `statusbar` | `StatusBarPlugin` | An indicator widget in the status bar |
| `controller` | `ControllerPlugin` | A device-specific control widget |
| `panel` | `PanelPlugin` | A dockable `BasePanel` |

`PlanPlugin` exists and is exported from `lightfall.plugins`, but the
`"plan"` type is not currently registered in the default startup sequence,
so manifest entries of type `"plan"` are skipped. The supported route for
adding plans today is user plan files in `~/lightfall/plans/` — see
[Plans](plans.md).

### User plugin files

Python files dropped into `~/lightfall/plugins/` are executed by
`UserPluginService` with hot-reload. Any non-abstract `PluginType` subclass
defined in such a file auto-registers via `PluginType.__init_subclass__` —
no manifest needed. The auto-registration path currently supports the
`agent` and `panel` types; other types log a warning. `BasePanel`
subclasses that self-register with `PanelRegistry` at module scope are also
tracked for unload. Each change to a user plugin file is committed to a
local git repository automatically.

## PluginType (base class)

All plugin types inherit from `lightfall.plugins.types.PluginType`.

| Member | Kind | Description |
|--------|------|-------------|
| `type_name` | ClassVar `str` | Unique type identifier (e.g. `"plan"`). Set by the plugin-type base class, not by your plugin. |
| `is_singleton` | ClassVar `bool` | Whether one instance exists per plugin. All current plugin types set `True`. |
| `name` | abstract property | Unique plugin name within the type. **Required.** |
| `description` | property | Human-readable description. Optional override. |
| `validate_class(plugin_class)` | classmethod | Type-specific validation; default is an `issubclass` check. |
| `get_introspection_data()` | method | Dict consumed by the agent's MCP tools. Each plugin type extends it. |

```{eval-rst}
.. autoclass:: lightfall.plugins.types.PluginType
   :members:
   :show-inheritance:
```

## AgentPlugin

`lightfall.plugins.agent_plugin.AgentPlugin` — extends the embedded Claude
agent with a skill prompt (materialized as a `SKILL.md`) and/or an
in-process MCP server. One settings toggle controls both contributions.

| Member | Kind | Description |
|--------|------|-------------|
| `name` | abstract property | **Required.** ≤64 chars, lowercase with hyphens/underscores. Becomes the SKILL.md name and the MCP server namespace (`mcp__<name>__*`). |
| `description` | abstract property | **Required.** One line; shown in settings UI and SKILL.md frontmatter (truncated to 1024 chars). |
| `get_system_prompt()` | method | SKILL.md body. Empty string (default) = no skill contribution. |
| `create_tools()` | method | List of `@tool`-decorated callables. Empty (default) = no MCP server contribution. |
| `get_references_dir()` | method | Optional package directory of supplementary docs, copied to the skill's `references/` dir at session start. |
| `display_name` | property | Settings-UI label. Defaults to title-cased `name`. |
| `category` | property | Settings-UI grouping (`general`, `devices`, `acquisition`, `operations`, `development`). Default `"general"`. |
| `enabled_by_default` | property | Default `True`. |
| `priority` | property | Settings-UI sort order (lower = first). Default `100`. |

A useful AgentPlugin overrides at least one of `get_system_prompt()` or
`create_tools()`.

```{eval-rst}
.. autoclass:: lightfall.plugins.agent_plugin.AgentPlugin
   :members:
   :show-inheritance:
   :member-order: bysource
```

## ControllerPlugin

`lightfall.plugins.controller_plugin.ControllerPlugin` — provides a
device-specific control widget. Controllers inspect the selected device
items and return a priority; the highest-priority match supplies the widget.

| Member | Kind | Description |
|--------|------|-------------|
| `name` | abstract property | **Required.** Unique controller name. |
| `can_control(items)` | abstract method | **Required.** Return an `int` priority for the given `DeviceTreeItem` selection, or `None` if not applicable. Suggested ranges: 200+ exact device/prefix match, 100–199 device class, 50–99 category, 1–49 generic fallback. |
| `create_widget(parent)` | abstract method | **Required.** Return a new widget; it should accept devices via a `set_items()` method (like `BaseControlWidget`). |
| `display_name` | property | Widget-selector label. Defaults to title-cased `name`. |

```{eval-rst}
.. autoclass:: lightfall.plugins.controller_plugin.ControllerPlugin
   :members:
   :show-inheritance:
   :member-order: bysource
```

## EnginePlugin

`lightfall.plugins.engine_plugin.EnginePlugin` — provides an execution
engine selectable through user preferences.

| Member | Kind | Description |
|--------|------|-------------|
| `name` | abstract property | **Required.** Engine identifier for registration and preferences. |
| `create_engine(**kwargs)` | abstract method | **Required.** Return a fully initialized `BaseEngine` instance. |
| `display_name` | property | UI label. Defaults to title-cased `name`. |
| `engine_description` | property | Longer description for the UI. Default empty. |

```{eval-rst}
.. autoclass:: lightfall.plugins.engine_plugin.EnginePlugin
   :members:
   :show-inheritance:
   :member-order: bysource
```

## PanelPlugin

`lightfall.plugins.panel_plugin.PanelPlugin` — wraps a `BasePanel` subclass
(see [Panels](panels.md)) and registers it with `PanelRegistry` on load.
Use `preload=True` in the manifest entry so the panel class is available
when the main window builds its sidebar.

| Member | Kind | Description |
|--------|------|-------------|
| `name` | abstract property | **Required.** Unique plugin name. |
| `get_panel_class()` | abstract method | **Required.** Return the `BasePanel` subclass. Import the panel class inside this method to keep manifest import light. |
| `panel_id` | property | Convenience: the wrapped panel's `panel_metadata.id`. |

```python
class MyPanelPlugin(PanelPlugin):
    @property
    def name(self) -> str:
        return "my_panel"

    def get_panel_class(self):
        from my_package.panels import MyPanel
        return MyPanel
```

```{eval-rst}
.. autoclass:: lightfall.plugins.panel_plugin.PanelPlugin
   :members:
   :show-inheritance:
   :member-order: bysource
```

## PlanPlugin

`lightfall.plugins.plan_plugin.PlanPlugin` — wraps a Bluesky plan generator
function with metadata for UI generation. As noted in the startup-types
table above, the `"plan"` manifest type is not registered in the current
startup sequence; prefer user plan files (see [Plans](plans.md)) for adding
plans today.

| Member | Kind | Description |
|--------|------|-------------|
| `name` | abstract property | **Required.** Plan name for registry and UI. |
| `get_plan_function()` | abstract method | **Required.** Return the plan generator function (yields Bluesky messages). |
| `category` | property | Grouping in the UI (e.g. `"scan"`, `"alignment"`). Default `"general"`. |
| `plan_description` | property | Defaults to the plan function's docstring. |
| `get_plan_info()` | method | Builds a `PlanInfo` (via `PlanInfo.from_function`) for `PlanRegistry`. |

```{eval-rst}
.. autoclass:: lightfall.plugins.plan_plugin.PlanPlugin
   :members:
   :show-inheritance:
   :member-order: bysource
```

## SettingsPlugin

`lightfall.plugins.settings_plugin.SettingsPlugin` — provides a page in the
Preferences dialog.

Lifecycle: instantiate → `on_loaded()` (before the main window for
`preload=True` entries) → on dialog open: `create_widget()` (once, cached)
then `load_settings()` → on user edits: `apply_preview()` → on OK/Apply:
`validate()` then `save_settings()` → on Cancel: `revert_preview()`.

| Member | Kind | Description |
|--------|------|-------------|
| `name` | abstract property | **Required.** Unique settings-page identifier. |
| `create_widget(parent)` | abstract method | **Required.** Build the settings widget (called once, cached). |
| `load_settings()` | abstract method | **Required.** Populate the widget from `PreferencesManager`. |
| `save_settings()` | abstract method | **Required.** Persist widget values to `PreferencesManager`. |
| `display_name` | property | Sidebar label. Defaults to title-cased `name`. |
| `icon` | property | Optional `QIcon` for the sidebar. Default `None`. |
| `category` | property | Sidebar grouping (`general`, `appearance`, `advanced`, `plugins`). Default `"general"`. |
| `priority` | property | Sort order within category (lower = first). Default `100`. |
| `validate()` | method | Return a list of error messages; non-empty blocks save. |
| `apply_preview()` / `revert_preview()` | methods | Optional live preview (e.g. theme changes) and its undo. |
| `on_loaded()` | method | Apply settings at startup (preload plugins run before the main window). |

```{eval-rst}
.. autoclass:: lightfall.plugins.settings_plugin.SettingsPlugin
   :members:
   :show-inheritance:
   :member-order: bysource
```

## StatusBarPlugin

`lightfall.plugins.statusbar_plugin.StatusBarPlugin` — an indicator in the
main window's status bar. The default widget is a flat `QToolButton`;
subclasses typically only implement `update()`, `connect_signals()`,
`disconnect_signals()`, and optionally `on_clicked()`, using the display
helpers to drive the button.

Subclasses must define a class-level `metadata: StatusBarPluginMetadata`
and, if they override `__init__`, call `super().__init__()`.

| Member | Kind | Description |
|--------|------|-------------|
| `metadata` | ClassVar | **Required.** `StatusBarPluginMetadata(id, name, description, priority, position, tooltip)`. `position` is `"left"`, `"right"`, or `"permanent"`; lower `priority` sits further left. |
| `name` | abstract property | **Required.** Unique plugin name. |
| `update()` | abstract method | **Required.** Refresh the display from current state. |
| `connect_signals()` / `disconnect_signals()` | abstract methods | **Required.** Wire/unwire the service signals that trigger `update()`. |
| `on_clicked()` | method | React to a click on the default button. Default no-op. |
| `create_widget(parent)` | method | Override for a custom widget; you must assign `self._widget` and handle click wiring yourself. |
| `set_text` / `set_icon` / `set_tooltip` / `set_color` | helpers | Drive the default button. |
| `set_visible(visible)` / `is_visible` / `visibility_changed` | visibility | Hidden widgets are removed from the layout flow. |

```{eval-rst}
.. autoclass:: lightfall.plugins.statusbar_plugin.StatusBarPluginMetadata
   :members:
   :show-inheritance:
```

```{eval-rst}
.. autoclass:: lightfall.plugins.statusbar_plugin.StatusBarPlugin
   :members:
   :show-inheritance:
   :member-order: bysource
```

## ThemePlugin

`lightfall.plugins.theme_plugin.ThemePlugin` — provides a color scheme.
Themes register with `ThemeRegistry` and appear in the Appearance
preferences.

| Member | Kind | Description |
|--------|------|-------------|
| `name` | abstract property | **Required.** Lowercase identifier (e.g. `"slate"`). |
| `display_name` | abstract property | **Required.** Label in the theme selector. |
| `is_dark` | abstract property | **Required.** Used for OS-level "System" theme selection. |
| `get_theme_definition()` | abstract method | **Required.** Return a `ThemeDefinition`. |

`ThemeDefinition` is a dataclass of color strings — `primary`, `secondary`,
`success`, `warning`, `error`, `info`, `background`, `surface`, `text`,
`text_secondary`, `border`, plus `connected`/`disconnected` (default to
`success`/`error`), `sea` (Islands-layout gap color, falls back to
`background`), and `css_overrides` (CSS appended after the base stylesheet).

```{eval-rst}
.. autoclass:: lightfall.plugins.theme_plugin.ThemeDefinition
   :members:
   :show-inheritance:
```

```{eval-rst}
.. autoclass:: lightfall.plugins.theme_plugin.ThemePlugin
   :members:
   :show-inheritance:
   :member-order: bysource
```

## Infrastructure

These classes implement plugin discovery and loading. Plugin authors rarely
interact with them directly beyond writing a manifest, but they define the
loading lifecycle: `DISCOVERED → QUEUED_LOAD → LOADING → QUEUED_INIT →
INITIALIZING → READY` (or `FAILED_LOAD` / `FAILED_INIT` / `DISABLED`).

### PluginManifest

```{eval-rst}
.. autoclass:: lightfall.plugins.manifest.PluginManifest
   :members:
   :show-inheritance:
```

### PluginEntry

```{eval-rst}
.. autoclass:: lightfall.plugins.manifest.PluginEntry
   :members:
   :show-inheritance:
```

### PluginRegistry

```{eval-rst}
.. autoclass:: lightfall.plugins.registry.PluginRegistry
   :members:
   :show-inheritance:
```

### PluginLoader

```{eval-rst}
.. autoclass:: lightfall.plugins.loader.PluginLoader
   :members:
   :show-inheritance:
```

### PluginInfo

```{eval-rst}
.. autoclass:: lightfall.plugins.info.PluginInfo
   :members:
   :show-inheritance:
```

### PluginStatus

```{eval-rst}
.. autoclass:: lightfall.plugins.errors.PluginStatus
   :members:
   :show-inheritance:
```

### Errors

```{eval-rst}
.. autoclass:: lightfall.plugins.errors.PluginError
   :show-inheritance:

.. autoclass:: lightfall.plugins.errors.PluginLoadError
   :show-inheritance:

.. autoclass:: lightfall.plugins.errors.PluginInitError
   :show-inheritance:

.. autoclass:: lightfall.plugins.errors.PluginNotFoundError
   :show-inheritance:

.. autoclass:: lightfall.plugins.errors.PluginTypeNotFoundError
   :show-inheritance:
```
