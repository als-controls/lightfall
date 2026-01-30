# SettingsPlugin

Settings plugins add preferences pages to the Settings dialog.

## Purpose

Use `SettingsPlugin` when you want to:
- Add configurable options for your feature
- Provide a UI for user preferences
- Store persistent settings

## Base Class

```python
from lucid.plugins.settings_plugin import SettingsPlugin
```

## Class Attributes

| Attribute | Value | Description |
|-----------|-------|-------------|
| `type_name` | `"settings"` | Plugin type identifier |
| `is_singleton` | `True` | One instance per plugin |

## Required Methods

### name (property)

Unique identifier for this settings plugin.

```python
@property
def name(self) -> str:
    return "my_settings"
```

### create_widget(parent)

Create the settings widget displayed in the preferences dialog.

```python
def create_widget(self, parent: QWidget | None = None) -> QWidget:
    """Create the settings widget.

    Args:
        parent: Parent widget (the dialog).

    Returns:
        QWidget containing the settings controls.
    """
    widget = QWidget(parent)
    # Add controls...
    return widget
```

### load_settings()

Populate the widget with current values from storage.

```python
def load_settings(self) -> None:
    """Load current settings into the widget."""
    prefs = PreferencesManager.get_instance()
    self._edit.setText(prefs.get("my_key", "default"))
```

### save_settings()

Save widget values to persistent storage.

```python
def save_settings(self) -> None:
    """Save widget values to storage."""
    prefs = PreferencesManager.get_instance()
    prefs.set("my_key", self._edit.text())
```

## Optional Methods

### display_name (property)

Human-readable name for the preferences sidebar. Defaults to title-cased `name`.

```python
@property
def display_name(self) -> str:
    return "My Settings"
```

### icon (property)

Optional icon for the sidebar.

```python
@property
def icon(self) -> QIcon | None:
    return QIcon.fromTheme("preferences-system")
```

### category (property)

Category for grouping in the sidebar. Default: `"general"`.

```python
@property
def category(self) -> str:
    return "general"  # or "advanced", "plugins", etc.
```

### priority (property)

Sort order within category (lower = higher in list). Default: `100`.

```python
@property
def priority(self) -> int:
    return 50  # Appears before default priority items
```

### validate()

Validate widget values before saving. Return error messages.

```python
def validate(self) -> list[str]:
    """Validate current widget values.

    Returns:
        List of error messages, or empty list if valid.
    """
    errors = []
    if not self._edit.text().strip():
        errors.append("Value cannot be empty")
    return errors
```

### apply_preview()

Apply settings temporarily for live preview (e.g., theme changes).

```python
def apply_preview(self) -> None:
    """Apply settings for live preview."""
    # Apply temporary changes for preview
    ThemeManager.get_instance().set_theme(self._theme_combo.currentData())
```

### revert_preview()

Revert preview changes when user cancels.

```python
def revert_preview(self) -> None:
    """Revert preview changes."""
    ThemeManager.get_instance().set_theme(self._original_theme)
```

### on_loaded()

Called when plugin is loaded. For preload plugins, this runs before the main window.

```python
def on_loaded(self) -> None:
    """Apply settings on startup."""
    prefs = PreferencesManager.get_instance()
    ThemeManager.get_instance().set_theme(prefs.theme)
```

## Lifecycle

1. Plugin is instantiated on load
2. `on_loaded()` is called (for preload plugins, before main window)
3. When Preferences dialog opens:
   - `create_widget()` is called (cached for reuse)
   - `load_settings()` populates widget
4. As user interacts:
   - `apply_preview()` provides live feedback
5. On OK/Apply:
   - `validate()` checks values
   - `save_settings()` persists values
6. On Cancel:
   - `revert_preview()` undoes preview changes

## Complete Example

```python
"""Beamline configuration settings plugin."""

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.ui.preferences.manager import PreferencesManager


class BeamlineSettingsPlugin(SettingsPlugin):
    """Settings for beamline-specific configuration."""

    def __init__(self) -> None:
        self._name_edit: QLineEdit | None = None
        self._sector_spin: QSpinBox | None = None
        self._mode_combo: QComboBox | None = None

    @property
    def name(self) -> str:
        return "beamline"

    @property
    def display_name(self) -> str:
        return "Beamline"

    @property
    def category(self) -> str:
        return "general"

    @property
    def priority(self) -> int:
        return 20  # After appearance (0), before others

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Beamline identification
        id_group = QGroupBox("Identification")
        id_layout = QFormLayout(id_group)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g., 7.0.1.1")
        id_layout.addRow("Beamline:", self._name_edit)

        self._sector_spin = QSpinBox()
        self._sector_spin.setRange(1, 12)
        id_layout.addRow("Sector:", self._sector_spin)

        layout.addWidget(id_group)

        # Operating mode
        mode_group = QGroupBox("Operating Mode")
        mode_layout = QFormLayout(mode_group)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Normal", "Commissioning", "Maintenance"])
        mode_layout.addRow("Mode:", self._mode_combo)

        layout.addWidget(mode_group)
        layout.addStretch()

        return widget

    def load_settings(self) -> None:
        prefs = PreferencesManager.get_instance()

        if self._name_edit:
            self._name_edit.setText(prefs.get("beamline_name", ""))

        if self._sector_spin:
            self._sector_spin.setValue(prefs.get("beamline_sector", 1))

        if self._mode_combo:
            mode = prefs.get("beamline_mode", "Normal")
            index = self._mode_combo.findText(mode)
            if index >= 0:
                self._mode_combo.setCurrentIndex(index)

    def save_settings(self) -> None:
        prefs = PreferencesManager.get_instance()

        if self._name_edit:
            prefs.set("beamline_name", self._name_edit.text())

        if self._sector_spin:
            prefs.set("beamline_sector", self._sector_spin.value())

        if self._mode_combo:
            prefs.set("beamline_mode", self._mode_combo.currentText())

    def validate(self) -> list[str]:
        errors = []

        if self._name_edit and not self._name_edit.text().strip():
            errors.append("Beamline name is required")

        return errors
```

## Registration

### Built-in Manifest

```python
PluginEntry(
    type_name="settings",
    name="beamline",
    import_path="my_package.settings:BeamlineSettingsPlugin",
),
```

### With Preload

For settings that must apply before the main window (e.g., theme):

```python
PluginEntry(
    type_name="settings",
    name="appearance",
    import_path="lucid.ui.preferences.builtin:AppearanceSettingsPlugin",
    preload=True,  # Load before main window
),
```

## Using PreferencesManager

`PreferencesManager` is the standard way to store settings:

```python
from lucid.ui.preferences.manager import PreferencesManager

prefs = PreferencesManager.get_instance()

# Read values
value = prefs.get("key", default_value)
theme = prefs.theme  # Built-in properties for common settings

# Write values
prefs.set("key", value)
prefs.theme = "dark"
```
