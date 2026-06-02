# External Plugin Packages

This guide covers creating standalone Python packages that provide Lightfall plugins. This is the recommended approach for beamline-specific customizations.

## When to Use External Packages

Use external packages when:
- Plugins are specific to a beamline or facility
- Plugins should be version-controlled separately
- Plugins need their own dependencies
- Plugins will be distributed to multiple installations

## Package Structure

A typical plugin package structure:

```
my-beamline-plugins/
├── pyproject.toml
├── README.md
├── src/
│   └── my_beamline/
│       ├── __init__.py
│       ├── manifest.py           # Plugin manifest
│       ├── plans/
│       │   ├── __init__.py
│       │   ├── alignment.py      # Alignment plan plugins
│       │   └── scans.py          # Scan plan plugins
│       ├── settings/
│       │   ├── __init__.py
│       │   └── beamline.py       # Beamline settings plugin
│       ├── controllers/
│       │   ├── __init__.py
│       │   └── slits.py          # Slit controller plugin
│       └── themes/
│           ├── __init__.py
│           └── beamline_dark.py  # Custom theme
```

## pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "my-beamline-plugins"
version = "1.0.0"
description = "Lightfall plugins for beamline 7.0.1.1"
requires-python = ">=3.11"
dependencies = [
    "lightfall>=1.0.0",  # Depend on Lightfall
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-qt>=4.2",
]

# Register plugin manifest via entry point
[project.entry-points."lightfall.plugins"]
my_beamline = "my_beamline.manifest:manifest"

[tool.hatch.build.targets.wheel]
packages = ["src/my_beamline"]
```

## Manifest Module

Create `src/my_beamline/manifest.py`:

```python
"""Plugin manifest for beamline 7.0.1.1."""

from lightfall.plugins import PluginManifest, PluginEntry

manifest = PluginManifest(
    name="beamline-7.0.1.1",
    version="1.0.0",
    description="Custom plugins for beamline 7.0.1.1",
    plugins=[
        # Plans
        PluginEntry(
            type_name="plan",
            name="bl_grid_scan",
            import_path="my_beamline.plans.scans:GridScanPlan",
        ),
        PluginEntry(
            type_name="plan",
            name="bl_alignment",
            import_path="my_beamline.plans.alignment:AlignmentPlan",
        ),

        # Settings
        PluginEntry(
            type_name="settings",
            name="beamline_config",
            import_path="my_beamline.settings.beamline:BeamlineConfigPlugin",
        ),

        # Controllers
        PluginEntry(
            type_name="controller",
            name="bl_slits",
            import_path="my_beamline.controllers.slits:SlitControllerPlugin",
        ),

        # Theme
        PluginEntry(
            type_name="theme",
            name="beamline_dark",
            import_path="my_beamline.themes.beamline_dark:BeamlineDarkTheme",
            preload=True,
        ),
    ],
)
```

## Example Plugin Implementations

### Settings Plugin

```python
# src/my_beamline/settings/beamline.py
"""Beamline configuration settings."""

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.manager import PreferencesManager


class BeamlineConfigPlugin(SettingsPlugin):
    """Settings plugin for beamline-specific configuration."""

    def __init__(self) -> None:
        self._name_edit: QLineEdit | None = None
        self._pv_prefix_edit: QLineEdit | None = None

    @property
    def name(self) -> str:
        return "beamline_config"

    @property
    def display_name(self) -> str:
        return "Beamline 7.0.1.1"

    @property
    def category(self) -> str:
        return "beamline"

    @property
    def priority(self) -> int:
        return 10  # High priority within beamline category

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)

        group = QGroupBox("Beamline Configuration")
        form = QFormLayout(group)

        self._name_edit = QLineEdit()
        form.addRow("Beamline Name:", self._name_edit)

        self._pv_prefix_edit = QLineEdit()
        self._pv_prefix_edit.setPlaceholderText("e.g., BL701:")
        form.addRow("PV Prefix:", self._pv_prefix_edit)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    def load_settings(self) -> None:
        prefs = PreferencesManager.get_instance()
        if self._name_edit:
            self._name_edit.setText(prefs.get("bl_name", "7.0.1.1"))
        if self._pv_prefix_edit:
            self._pv_prefix_edit.setText(prefs.get("bl_pv_prefix", "BL701:"))

    def save_settings(self) -> None:
        prefs = PreferencesManager.get_instance()
        if self._name_edit:
            prefs.set("bl_name", self._name_edit.text())
        if self._pv_prefix_edit:
            prefs.set("bl_pv_prefix", self._pv_prefix_edit.text())
```

### Plan Plugin

```python
# src/my_beamline/plans/scans.py
"""Beamline-specific scan plans."""

from typing import Any, Callable, Generator

from lightfall.plugins.plan_plugin import PlanPlugin


class GridScanPlan(PlanPlugin):
    """Grid scan plan optimized for this beamline."""

    @property
    def name(self) -> str:
        return "bl_grid_scan"

    @property
    def category(self) -> str:
        return "beamline"

    def get_plan_function(self) -> Callable[..., Generator[Any, Any, Any]]:
        return self._grid_scan

    def _grid_scan(
        self,
        detectors: list,
        motor1,
        start1: float,
        stop1: float,
        num1: int,
        motor2,
        start2: float,
        stop2: float,
        num2: int,
    ):
        """Perform a grid scan over two motors.

        Args:
            detectors: Detectors to read at each point.
            motor1: First motor to scan.
            start1: Start position for motor1.
            stop1: Stop position for motor1.
            num1: Number of points for motor1.
            motor2: Second motor to scan.
            start2: Start position for motor2.
            stop2: Stop position for motor2.
            num2: Number of points for motor2.
        """
        import bluesky.plans as bp

        yield from bp.grid_scan(
            detectors,
            motor1, start1, stop1, num1,
            motor2, start2, stop2, num2,
        )
```

### Controller Plugin

```python
# src/my_beamline/controllers/slits.py
"""Slit controller plugin."""

from PySide6.QtWidgets import QWidget

from lightfall.plugins.controller_plugin import ControllerPlugin


class SlitControllerPlugin(ControllerPlugin):
    """Controller for beamline slit devices."""

    @property
    def name(self) -> str:
        return "bl_slits"

    @property
    def display_name(self) -> str:
        return "Beamline Slits"

    def can_control(self, items) -> int | None:
        """Check if this controller handles the selected items."""
        if len(items) != 1:
            return None

        item = items[0]
        if item.device_info and "slit" in item.device_info.prefix.lower():
            return 200  # High priority for exact match
        return None

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        from my_beamline.widgets.slit_widget import SlitWidget
        return SlitWidget(parent)
```

## Installation

### Development Installation

```bash
# Clone the package
git clone https://gitlab.lbl.gov/beamlines/my-beamline-plugins.git
cd my-beamline-plugins

# Install in development mode
pip install -e ".[dev]"
```

### Production Installation

```bash
pip install my-beamline-plugins
```

Or from git:

```bash
pip install "my-beamline-plugins @ git+https://gitlab.lbl.gov/beamlines/my-beamline-plugins.git"
```

## Testing

### Test Plugin Loading

```python
# tests/test_manifest.py
"""Test that plugins load correctly."""

import pytest


def test_manifest_loads():
    """Test manifest can be imported."""
    from my_beamline.manifest import manifest

    assert manifest.name == "beamline-7.0.1.1"
    assert len(manifest.plugins) > 0


def test_plugins_importable():
    """Test all plugin classes can be imported."""
    from my_beamline.manifest import manifest

    for entry in manifest.plugins:
        module_path, class_name = entry.import_path.rsplit(":", 1)
        module = __import__(module_path, fromlist=[class_name])
        plugin_class = getattr(module, class_name)
        assert plugin_class is not None
```

### Test with Lightfall

```python
# tests/test_integration.py
"""Integration tests with Lightfall."""

import pytest
from lightfall.plugins.loader import PluginLoader
from lightfall.plugins.registry import PluginRegistry


@pytest.fixture
def loader():
    """Create a plugin loader."""
    registry = PluginRegistry()
    loader = PluginLoader(registry)
    # Register plugin types...
    return loader


def test_manifest_discovery(loader):
    """Test manifest is discovered via entry points."""
    count = loader.discover_manifests()
    assert count >= 1  # At least our manifest

    # Check our plugins are queued
    plugins = loader.registry.get_by_type("plan")
    names = [p.name for p in plugins]
    assert "bl_grid_scan" in names
```

## Version Compatibility

### Specifying Lightfall Version

In `pyproject.toml`, specify compatible Lightfall versions:

```toml
dependencies = [
    "lightfall>=1.0.0,<2.0.0",  # Compatible with Lightfall 1.x
]
```

### Handling API Changes

Use feature detection for optional features:

```python
def get_plan_function(self):
    # Check for new API
    try:
        from lightfall.acquire.plans import enhanced_scan
        return enhanced_scan
    except ImportError:
        # Fall back to standard scan
        import bluesky.plans as bp
        return bp.scan
```

## Best Practices

1. **Namespace your plugins**: Use a prefix like `bl_` to avoid name conflicts
2. **Handle import errors gracefully**: Plugins should not crash the application
3. **Document dependencies**: List any extra packages in pyproject.toml
4. **Version your manifest**: Use semantic versioning for your package
5. **Write tests**: Test that plugins load and function correctly
6. **Use preload sparingly**: Only for plugins that must run before the main window
