# API Reference

Formal reference for the surfaces external authors use when extending
Lightfall: plugin base classes, the panel framework, plan authoring, and
the visualization contract.

Pages combine hand-written reference tables (verified against the source
in `src/lightfall/`) with autodoc listings generated from docstrings.

```{toctree}
:maxdepth: 2
:caption: API Reference

plugins
panels
plans
visualization
```

## Map of the reference

| Page | Covers | Key classes |
|------|--------|-------------|
| [Plugin Types](plugins.md) | The plugin system: base class, all plugin-type contracts, manifest registration | `PluginType`, `AgentPlugin`, `PanelPlugin`, `PlanPlugin`, `SettingsPlugin`, `ControllerPlugin`, `EnginePlugin`, `StatusBarPlugin`, `ThemePlugin`, `PluginManifest`, `PluginEntry` |
| [Panels](panels.md) | The panel framework: metadata, lifecycle, title-bar injection, introspection | `BasePanel`, `PanelMetadata`, `PanelStatus`, `PanelRegistry` |
| [Plans](plans.md) | Plan authoring: registry, parameter introspection, UI annotations, user plans, embedded plan UIs | `PlanRegistry`, `PlanInfo`, `ParameterInfo`, `Unit`/`Range`/`DeviceFilter`, `plan_with_ui` |
| [Visualization](visualization.md) | The visualization widget contract and selection flow | `BaseVisualization`, `VisualizationRegistry` |

For tutorials and step-by-step guides, see the
[plugin developer guide](../developer-guide/plugins/index.md). This section
is the reference companion: exact members, signatures, and contracts.

## Stability

Lightfall is pre-1.0. No formal API stability guarantee exists yet, but the
classes documented here are the de-facto stable authoring surface: they are
what the built-in plugins, panels, and plans are written against, and what
external packages (beamline plugin distributions) import. Changes to these
classes are deliberate and recorded in the
[changelog](https://github.com/als-controls/lightfall/blob/main/CHANGELOG.md).
Internal modules not listed here may change without notice.
