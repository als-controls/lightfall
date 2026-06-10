# Plugin System Overview

Lightfall's plugin system enables extending the application with custom functionality without modifying core code. Plugins are discovered automatically via Python entry points and loaded using a manifest-based system.

## Architecture

The plugin system consists of:

1. **Plugin Types**: Abstract base classes defining interfaces (e.g., `SettingsPlugin`, `PanelPlugin`)
2. **Plugin Manifests**: Collections of plugin entries that declare available plugins
3. **Plugin Loader**: Discovers manifests via entry points and loads plugins in the background
4. **Plugin Registry**: Tracks all registered plugins by type and name

```
┌─────────────────────────────────────────────────────────────────┐
│                         Application                              │
├─────────────────────────────────────────────────────────────────┤
│  PluginLoader                                                    │
│  ├── Discovers manifests via entry points                       │
│  ├── Loads plugin classes (background thread)                   │
│  └── Instantiates plugins                                        │
├─────────────────────────────────────────────────────────────────┤
│  PluginRegistry                                                  │
│  ├── Tracks plugins by type:name                                │
│  └── Provides lookup APIs                                        │
├─────────────────────────────────────────────────────────────────┤
│  Plugin Types                                                    │
│  ├── SettingsPlugin    - Preferences pages                       │
│  ├── PanelPlugin       - UI panels                              │
│  ├── PlanPlugin        - Bluesky plans                          │
│  ├── EnginePlugin      - Execution backends                     │
│  ├── ThemePlugin       - Color themes                           │
│  ├── StatusBarPlugin   - Status indicators                      │
│  ├── ControllerPlugin  - Device control widgets                 │
│  └── AgentPlugin       - Claude assistant expertise and tools   │
└─────────────────────────────────────────────────────────────────┘
```

## Plugin Types

Lightfall supports 8 plugin types, each serving a specific purpose:

| Type | Base Class | Purpose | Singleton |
|------|------------|---------|-----------|
| `settings` | `SettingsPlugin` | Add preferences pages | Yes |
| `panel` | `PanelPlugin` | Add application panels | Yes |
| `plan` | `PlanPlugin` | Register Bluesky plans | Yes |
| `engine` | `EnginePlugin` | Provide execution backends | Yes |
| `theme` | `ThemePlugin` | Define color themes | Yes |
| `statusbar` | `StatusBarPlugin` | Add status bar indicators | Yes |
| `controller` | `ControllerPlugin` | Device-specific control widgets | Yes |
| `agent` | `AgentPlugin` | Claude assistant expertise and tools | Yes |

See [Plugin Type Reference](plugin-types/index.md) for detailed documentation on each type.

## Loading Lifecycle

Plugins go through these states during loading:

1. **DISCOVERED**: Manifest entry found, not yet loaded
2. **QUEUED_LOAD**: Waiting in load queue
3. **LOADING**: Class being imported
4. **QUEUED_INIT**: Class loaded, waiting for instantiation
5. **INITIALIZING**: Instance being created
6. **READY**: Plugin fully loaded and available
7. **FAILED_LOAD** / **FAILED_INIT**: Error during loading
8. **DISABLED**: User disabled this plugin

### Preload Plugins

Plugins with `preload=True` are loaded synchronously before the main window is created. Use this for plugins that must be ready immediately, such as:

- Theme plugins (to apply colors before any UI appears)
- Appearance settings (to load saved theme preference)

```python
PluginEntry(
    type_name="settings",
    name="appearance",
    import_path="lightfall.ui.preferences.builtin:AppearanceSettingsPlugin",
    preload=True,  # Load before main window
)
```

## Manifest System

Plugins are declared in manifests, which are collections of `PluginEntry` objects:

```python
from lightfall.plugins import PluginManifest, PluginEntry

manifest = PluginManifest(
    name="my-beamline-plugins",
    version="1.0.0",
    description="Custom plugins for beamline 7.0.1.1",
    plugins=[
        PluginEntry(
            type_name="plan",
            name="my_scan",
            import_path="my_beamline.plans:MyScanPlan",
        ),
        PluginEntry(
            type_name="settings",
            name="beamline_config",
            import_path="my_beamline.settings:BeamlineConfigPlugin",
        ),
    ],
)
```

### Built-in Manifest

Lightfall's built-in plugins are defined in `lightfall.plugins.builtin_manifest`. This manifest is loaded directly by the application and contains core plugins like:

- Theme plugins (light, slate, darkblue, islands, and others)
- Settings plugins (appearance, devices, tiled, logbook, ipc, claude, etc.)
- Engine plugins (bluesky, mock)
- Panel plugins (devices, bluesky, queue, claude, logbook, tiled_browser, etc.)
- Status bar plugins (user_status, tiled_status, als_beam_status, thread_status, nats_status, etc.)
- Agent plugins (device_tools, plan_tools, panel_design, etc.)

### External Package Manifests

External packages can provide plugins via entry points. See [External Packages](external-packages.md) for details.

## Audit

Because plugins are distributed as ordinary Python packages in version-controlled repositories, a beamline's customization is auditable with the tools the lab already uses — no separate audit pipeline. The git history of a beamline's plugin repository is the record of what changed, when, and by whom:

- **Review recent changes** — `git log` on the plugin repository.
- **Roll back a single change** — `git revert <sha>`.
- **Compare two states** — `git diff <a>..<b>`.

This is the foundation the design-mode workflow builds on: staff-authored interface changes land as commits in the beamline plugin repository, so every change is reviewable and reversible by construction.

## Quick Links

- [Quickstart Guide](quickstart.md) - Create your first plugin
- [Creating Plugins](creating-plugins.md) - Step-by-step plugin creation
- [Manifest Reference](manifest-reference.md) - Complete manifest documentation
- [External Packages](external-packages.md) - Create plugin packages

```{toctree}
:maxdepth: 2
:caption: Plugin Documentation

quickstart
creating-plugins
manifest-reference
external-packages
plugin-types/index
```
