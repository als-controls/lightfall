# Manifest Reference

This document provides complete reference for plugin manifests and entry points.

## PluginEntry

A `PluginEntry` defines a single plugin within a manifest.

```python
from lucid.plugins import PluginEntry

entry = PluginEntry(
    type_name="settings",
    name="my_settings",
    import_path="my_package.plugins:MySettingsPlugin",
    metadata={"priority": 10},
    preload=False,
)
```

### Attributes

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `type_name` | `str` | Yes | Plugin type identifier (e.g., "settings", "panel") |
| `name` | `str` | Yes | Unique name within this type |
| `import_path` | `str` | Yes | Python import path in format `module.path:ClassName` |
| `metadata` | `dict` | No | Additional plugin-specific metadata |
| `preload` | `bool` | No | If `True`, load before main window (default: `False`) |

### import_path Format

The `import_path` must be in the format `module.path:ClassName`:

```python
# Correct
"my_package.plugins.settings:MySettingsPlugin"
"lucid.ui.preferences.builtin:AppearanceSettingsPlugin"

# Incorrect - missing class name
"my_package.plugins.settings"

# Incorrect - wrong separator
"my_package.plugins.settings.MySettingsPlugin"
```

### unique_id Property

Each entry has a `unique_id` property combining type and name:

```python
entry = PluginEntry("settings", "my_settings", "...")
print(entry.unique_id)  # "settings:my_settings"
```

### Preload Plugins

Plugins with `preload=True` are loaded synchronously before the main window:

```python
# Theme should apply before any windows appear
PluginEntry(
    type_name="theme",
    name="light",
    import_path="lucid.ui.theme.builtin:LightThemePlugin",
    preload=True,
)

# Appearance settings loads saved theme on startup
PluginEntry(
    type_name="settings",
    name="appearance",
    import_path="lucid.ui.preferences.builtin:AppearanceSettingsPlugin",
    preload=True,
)
```

Use preload for:
- Themes (must apply before UI renders)
- Settings that affect initial appearance
- Panels that must be registered before window creation

## PluginManifest

A `PluginManifest` groups multiple `PluginEntry` objects from a single source.

```python
from lucid.plugins import PluginManifest, PluginEntry

manifest = PluginManifest(
    name="my-beamline-plugins",
    version="1.0.0",
    description="Custom plugins for beamline 7.0.1.1",
    plugins=[
        PluginEntry("plan", "my_scan", "my_beamline.plans:MyScanPlan"),
        PluginEntry("plan", "my_align", "my_beamline.plans:MyAlignPlan"),
        PluginEntry("settings", "beamline_config", "my_beamline.settings:Config"),
    ],
    metadata={"author": "Beamline Team"},
)
```

### Attributes

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | `str` | Yes | Manifest identifier |
| `version` | `str` | No | Version string (default: "0.0.0") |
| `description` | `str` | No | Human-readable description |
| `plugins` | `list[PluginEntry]` | No | List of plugin entries |
| `metadata` | `dict` | No | Additional metadata |

### Methods

#### get_plugins_by_type(type_name)

Filter plugins by type:

```python
plans = manifest.get_plugins_by_type("plan")
# Returns list of PluginEntry with type_name="plan"
```

#### get_plugin_types()

Get all unique plugin types in the manifest:

```python
types = manifest.get_plugin_types()
# Returns {"plan", "settings"}
```

## Entry Points

External packages register manifests via Python entry points in `pyproject.toml`:

```toml
[project.entry-points."lucid.plugins"]
my_beamline = "my_beamline.manifest:manifest"
```

### Entry Point Group

All LUCID plugin manifests use the group `lucid.plugins`.

### Entry Point Format

- **Name**: Any unique identifier for your manifest
- **Value**: Python import path to a `PluginManifest` instance

```toml
# Format: name = "module.path:variable_name"
[project.entry-points."lucid.plugins"]
my_plugins = "my_package.manifest:manifest"
other_plugins = "my_package.other:other_manifest"
```

### Manifest Module Example

```python
# my_package/manifest.py
from lucid.plugins import PluginManifest, PluginEntry

manifest = PluginManifest(
    name="my-package",
    version="1.0.0",
    plugins=[
        PluginEntry("plan", "my_scan", "my_package.plans:MyScanPlan"),
    ],
)
```

## Discovery Process

On startup, `PluginLoader` discovers manifests:

1. Load the built-in manifest directly
2. Discover entry points in group `lucid.plugins`
3. Load each entry point to get its `PluginManifest`
4. Process all plugin entries from all manifests
5. Queue plugins for loading

```python
# Simplified discovery code
from importlib.metadata import entry_points

eps = entry_points(group="lucid.plugins")
for ep in eps:
    manifest = ep.load()  # Returns PluginManifest instance
    for entry in manifest.plugins:
        # Queue plugin for loading
        ...
```

## Complete Example

### Package Structure

```
my_beamline/
├── pyproject.toml
├── src/
│   └── my_beamline/
│       ├── __init__.py
│       ├── manifest.py      # Plugin manifest
│       ├── plans/
│       │   ├── __init__.py
│       │   └── scans.py     # Plan plugins
│       └── settings/
│           ├── __init__.py
│           └── config.py    # Settings plugin
```

### pyproject.toml

```toml
[project]
name = "my-beamline"
version = "1.0.0"
dependencies = ["lucid"]

[project.entry-points."lucid.plugins"]
my_beamline = "my_beamline.manifest:manifest"
```

### manifest.py

```python
from lucid.plugins import PluginManifest, PluginEntry

manifest = PluginManifest(
    name="my-beamline",
    version="1.0.0",
    description="Beamline 7.0.1.1 plugins",
    plugins=[
        PluginEntry(
            type_name="plan",
            name="grid_scan",
            import_path="my_beamline.plans.scans:GridScanPlan",
        ),
        PluginEntry(
            type_name="settings",
            name="beamline_config",
            import_path="my_beamline.settings.config:BeamlineConfigPlugin",
        ),
    ],
)
```

See [External Packages](external-packages.md) for more details on creating distributable plugin packages.
