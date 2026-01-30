# Creating Plugins

This guide provides detailed steps for creating LUCID plugins of any type.

## Plugin Creation Process

### 1. Choose the Right Plugin Type

Select the plugin type that matches your goal:

| Goal | Plugin Type |
|------|-------------|
| Add a preferences page | `SettingsPlugin` |
| Add a UI panel | `PanelPlugin` |
| Add a Bluesky plan | `PlanPlugin` |
| Add an execution backend | `EnginePlugin` |
| Add a color theme | `ThemePlugin` |
| Add a status indicator | `StatusBarPlugin` |
| Add device control widgets | `ControllerPlugin` |
| Add Claude assistant tools | `MCPToolPlugin` |
| Add Claude assistant expertise | `SkillPlugin` |

### 2. Create the Plugin Class

All plugins inherit from a base class in `lucid.plugins`:

```python
from lucid.plugins.<type>_plugin import <Type>Plugin

class MyPlugin(<Type>Plugin):
    @property
    def name(self) -> str:
        """Required: Unique identifier within this type."""
        return "my_plugin"

    # ... implement required methods
```

### 3. Implement Required Methods

Each plugin type has specific required methods. Check the [Plugin Type Reference](plugin-types/index.md) for your type.

Common patterns:

```python
# All plugins need a name property
@property
def name(self) -> str:
    return "my_plugin"

# Most plugins have optional display_name
@property
def display_name(self) -> str:
    return "My Plugin"  # Human-readable name
```

### 4. Register the Plugin

#### Option A: Built-in Manifest (Development)

For plugins being developed within LUCID, add to `builtin_manifest.py`:

```python
PluginEntry(
    type_name="settings",
    name="my_plugin",
    import_path="lucid.ui.preferences.my_plugin:MyPlugin",
),
```

#### Option B: External Package (Distribution)

For plugins in separate packages, use entry points. See [External Packages](external-packages.md).

### 5. Test the Plugin

1. Start LUCID
2. Verify the plugin loads (check logs for errors)
3. Test the plugin's functionality

## Common Patterns

### Accessing Services

Plugins often need access to application services. Import them lazily to avoid circular imports:

```python
class MyPlugin(SettingsPlugin):
    def save_settings(self) -> None:
        # Import inside method to avoid circular imports
        from lucid.ui.preferences.manager import PreferencesManager

        prefs = PreferencesManager.get_instance()
        prefs.set("my_key", self._value)
```

### Storing Plugin State

Use `__init__` to initialize instance variables:

```python
class MyPlugin(SettingsPlugin):
    def __init__(self) -> None:
        self._widget: QWidget | None = None
        self._some_control: QLineEdit | None = None
```

### Deferred Imports

Use `TYPE_CHECKING` for type hints that would cause circular imports:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lucid.ui.panels.base import BasePanel

class MyPanelPlugin(PanelPlugin):
    def get_panel_class(self) -> type[BasePanel]:
        # Import here to defer loading
        from lucid.ui.panels.my_panel import MyPanel
        return MyPanel
```

### Error Handling

Plugins should handle errors gracefully:

```python
def create_widget(self, parent: QWidget | None = None) -> QWidget:
    try:
        # Create widget...
        return widget
    except Exception as e:
        from loguru import logger
        logger.error("Failed to create widget: {}", e)
        # Return a fallback widget
        return QLabel(f"Error: {e}", parent)
```

## Plugin Type Quick Reference

### SettingsPlugin

```python
from lucid.plugins.settings_plugin import SettingsPlugin

class MySettingsPlugin(SettingsPlugin):
    @property
    def name(self) -> str:
        return "my_settings"

    def create_widget(self, parent=None) -> QWidget:
        # Return settings widget
        ...

    def load_settings(self) -> None:
        # Load values into widget
        ...

    def save_settings(self) -> None:
        # Save values from widget
        ...
```

### PanelPlugin

```python
from lucid.plugins.panel_plugin import PanelPlugin

class MyPanelPlugin(PanelPlugin):
    @property
    def name(self) -> str:
        return "my_panel"

    def get_panel_class(self) -> type[BasePanel]:
        from my_package.panels import MyPanel
        return MyPanel
```

### PlanPlugin

```python
from lucid.plugins.plan_plugin import PlanPlugin

class MyScanPlugin(PlanPlugin):
    @property
    def name(self) -> str:
        return "my_scan"

    @property
    def category(self) -> str:
        return "custom"

    def get_plan_function(self):
        return self._my_scan

    def _my_scan(self, motor, start, stop, num):
        """My custom scan plan."""
        import bluesky.plans as bp
        yield from bp.scan([], motor, start, stop, num)
```

### ThemePlugin

```python
from lucid.plugins.theme_plugin import ThemePlugin, ThemeDefinition

class MyThemePlugin(ThemePlugin):
    @property
    def name(self) -> str:
        return "my_theme"

    @property
    def display_name(self) -> str:
        return "My Theme"

    @property
    def is_dark(self) -> bool:
        return True

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            background="#1a1a1a",
            surface="#2a2a2a",
            text="#e0e0e0",
            primary="#3b82f6",
        )
```

See [Plugin Type Reference](plugin-types/index.md) for complete documentation on each type.
