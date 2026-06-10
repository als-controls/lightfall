# Plugin Quickstart

Create your first Lightfall plugin in 5 minutes. This guide walks through creating a minimal settings plugin that adds a preferences page.

## Prerequisites

- Lightfall installed in development mode
- Basic Python and Qt knowledge

## Step 1: Create the Plugin Class

Create a new file `my_settings_plugin.py`:

```python
"""My first Lightfall plugin."""

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lightfall.plugins.settings_plugin import SettingsPlugin


class MySettingsPlugin(SettingsPlugin):
    """A minimal settings plugin example."""

    @property
    def name(self) -> str:
        """Unique identifier for this plugin."""
        return "my_settings"

    @property
    def display_name(self) -> str:
        """Name shown in preferences sidebar."""
        return "My Settings"

    @property
    def category(self) -> str:
        """Category for grouping in sidebar."""
        return "general"

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget."""
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Hello from my plugin!"))
        return widget

    def load_settings(self) -> None:
        """Load current settings into the widget."""
        pass  # No settings to load yet

    def save_settings(self) -> None:
        """Save widget values to storage."""
        pass  # No settings to save yet
```

## Step 2: Register in the Built-in Manifest

For development, the easiest approach is to add your plugin to the built-in manifest. Edit `ncs/src/lightfall/plugins/builtin_manifest.py`:

```python
# Add to the plugins list:
PluginEntry(
    type_name="settings",
    name="my_settings",
    import_path="path.to.my_settings_plugin:MySettingsPlugin",
),
```

Replace `path.to.my_settings_plugin` with the actual import path to your file.

## Step 3: Test Your Plugin

1. Run Lightfall:
   ```bash
   lightfall
   ```

2. Open Preferences (Ctrl+, or File > Preferences)

3. Your "My Settings" page should appear in the sidebar

## What's Happening

1. On startup, `PluginLoader` processes the built-in manifest
2. It finds your `PluginEntry` and queues it for loading
3. Your plugin class is imported and instantiated
4. When you open Preferences, `create_widget()` is called
5. Your widget appears in the preferences dialog

## Next Steps

Now that you have a working plugin:

1. **Add real functionality**: See the [SettingsPlugin reference](plugin-types/settings.md) for the full interface
2. **Create a package**: See [External Packages](external-packages.md) to distribute your plugins
3. **Try other plugin types**: Browse the [Plugin Type Reference](plugin-types/index.md)

## Complete Example with Preferences

Here's a more complete example that actually stores preferences:

```python
"""Settings plugin that stores a preference value."""

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.manager import PreferencesManager


class BeamlineNamePlugin(SettingsPlugin):
    """Settings plugin for beamline name configuration."""

    def __init__(self) -> None:
        self._name_edit: QLineEdit | None = None

    @property
    def name(self) -> str:
        return "beamline_name"

    @property
    def display_name(self) -> str:
        return "Beamline"

    @property
    def category(self) -> str:
        return "general"

    @property
    def priority(self) -> int:
        return 50  # After appearance (0) but before others (100)

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)

        group = QGroupBox("Beamline Configuration")
        form = QFormLayout(group)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g., 7.0.1.1")
        form.addRow("Beamline Name:", self._name_edit)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    def load_settings(self) -> None:
        """Load the saved beamline name."""
        if self._name_edit:
            prefs = PreferencesManager.get_instance()
            name = prefs.get("beamline_name", "")
            self._name_edit.setText(name)

    def save_settings(self) -> None:
        """Save the beamline name."""
        if self._name_edit:
            prefs = PreferencesManager.get_instance()
            prefs.set("beamline_name", self._name_edit.text())

    def validate(self) -> list[str]:
        """Validate the beamline name is not empty."""
        errors = []
        if self._name_edit and not self._name_edit.text().strip():
            errors.append("Beamline name cannot be empty")
        return errors
```

This example demonstrates:
- Storing state in `__init__`
- Using `PreferencesManager` to load/save values
- Validation with error messages
- Priority ordering in the sidebar
