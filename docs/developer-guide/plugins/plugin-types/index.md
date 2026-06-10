# Plugin Type Reference

This section provides detailed documentation for each plugin type in Lightfall.

## Overview

| Type | Base Class | Purpose | Singleton |
|------|------------|---------|-----------|
| [`settings`](settings.md) | `SettingsPlugin` | Add preferences pages | Yes |
| [`panel`](panel.md) | `PanelPlugin` | Add application panels | Yes |
| [`plan`](plan.md) | `PlanPlugin` | Register Bluesky plans | Yes |
| [`engine`](engine.md) | `EnginePlugin` | Provide execution backends | Yes |
| [`theme`](theme.md) | `ThemePlugin` | Define color themes | Yes |
| [`statusbar`](statusbar.md) | `StatusBarPlugin` | Add status bar indicators | Yes |
| [`controller`](controller.md) | `ControllerPlugin` | Device-specific control widgets | Yes |
| [`agent`](agent.md) | `AgentPlugin` | Extend the embedded Claude agent: skill prompts and/or MCP tools | Yes |

## Common Interface

All plugin types inherit from `PluginType` and share these characteristics:

### Class Attributes

```python
class PluginType(ABC):
    type_name: ClassVar[str] = "base"       # Unique type identifier
    is_singleton: ClassVar[bool] = False    # One instance per plugin?

    @property
    def description(self) -> str:           # Human-readable description
        return "Base plugin type"
```

`AgentPlugin` additionally makes `description` abstract — agent plugins must provide it, since it doubles as the SKILL.md frontmatter description.

### Required Property

```python
@property
@abstractmethod
def name(self) -> str:
    """Unique identifier within this plugin type."""
    ...
```

### Optional Methods

```python
def get_introspection_data(self) -> dict[str, Any]:
    """Return plugin metadata for debugging/MCP tools."""
    return {
        "type": self.type_name,
        "name": self.name,
        "class": self.__class__.__name__,
        "module": self.__class__.__module__,
    }
```

## Choosing a Plugin Type

### UI Extensions

| Goal | Plugin Type |
|------|-------------|
| Add a preferences page | [`SettingsPlugin`](settings.md) |
| Add a new panel/dock widget | [`PanelPlugin`](panel.md) |
| Add custom device control widget | [`ControllerPlugin`](controller.md) |
| Add a status indicator | [`StatusBarPlugin`](statusbar.md) |
| Add a color theme | [`ThemePlugin`](theme.md) |

### Data Acquisition

| Goal | Plugin Type |
|------|-------------|
| Add a Bluesky scan plan | [`PlanPlugin`](plan.md) |
| Add an execution backend | [`EnginePlugin`](engine.md) |

### Claude Agent

| Goal | Plugin Type |
|------|-------------|
| Add domain expertise (skill prompt) and/or tools Claude can call | [`AgentPlugin`](agent.md) |

## Documentation Template

Each plugin type page follows this structure:

1. **Purpose** - What this plugin type is for
2. **Base Class** - Import path and class name
3. **Class Attributes** - Type-specific class attributes
4. **Required Methods** - Methods you must implement
5. **Optional Methods** - Methods you can override
6. **Lifecycle** - When methods are called
7. **Complete Example** - Working implementation
8. **Registration** - How to register the plugin

```{toctree}
:maxdepth: 1
:caption: Plugin Types

settings
panel
plan
engine
theme
statusbar
controller
agent
```
