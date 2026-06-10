# PanelPlugin

Panel plugins add new UI panels (dock widgets) to the application.

## Purpose

Use `PanelPlugin` when you want to:
- Add a new tool panel to the application
- Provide a custom view for data or devices
- Create reusable UI components

## Base Class

```python
from lightfall.plugins.panel_plugin import PanelPlugin
```

## Class Attributes

| Attribute | Value | Description |
|-----------|-------|-------------|
| `type_name` | `"panel"` | Plugin type identifier |
| `is_singleton` | `True` | One plugin instance (panel class can create multiple instances) |

## Required Methods

### name (property)

Unique identifier for this panel plugin.

```python
@property
def name(self) -> str:
    return "my_panel"
```

### get_panel_class()

Return the `BasePanel` subclass this plugin provides.

```python
def get_panel_class(self) -> type[BasePanel]:
    """Return the panel class.

    Returns:
        A BasePanel subclass.
    """
    from my_package.panels import MyPanel
    return MyPanel
```

## Optional Methods

### panel_id (property)

Get the panel ID from the panel class metadata. Usually not overridden.

```python
@property
def panel_id(self) -> str:
    return self.get_panel_class().panel_metadata.id
```

## Lifecycle

1. Plugin is instantiated on load (recommended: `preload=True`)
2. `get_panel_class()` is called to get the `BasePanel` subclass
3. Panel class is registered with `PanelRegistry`
4. Panel can be instantiated via `PanelRegistry.create(panel_id)`
5. Panel instances are managed by the docking system

## Creating a Panel Class

Panel plugins wrap `BasePanel` subclasses. Here's how to create one:

### Panel Metadata

Every panel needs metadata:

```python
from lightfall.ui.panels.base import BasePanel, PanelMetadata

class MyPanel(BasePanel):
    panel_metadata = PanelMetadata(
        id="my_package.panels.my_panel",      # Unique identifier
        name="My Panel",                       # Display name
        description="Description of my panel", # Tooltip
        category="tools",                      # Category for menu
        icon="mdi.wrench",                    # Icon name (qtawesome)
        singleton=True,                        # Only one instance allowed?
    )
```

### Panel Implementation

```python
from PySide6.QtWidgets import QLabel, QVBoxLayout

from lightfall.ui.panels.base import BasePanel, PanelMetadata


class MyPanel(BasePanel):
    """A custom panel for displaying data."""

    panel_metadata = PanelMetadata(
        id="my_package.panels.data_viewer",
        name="Data Viewer",
        description="View and analyze data",
        category="analysis",
        icon="mdi.chart-line",
        singleton=False,  # Allow multiple instances
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Data Viewer Panel"))
        # Add more widgets...
```

## Complete Example

### Panel Class

```python
# my_package/panels/monitor_panel.py
"""Real-time device monitor panel."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QTableWidget, QVBoxLayout

from lightfall.ui.panels.base import BasePanel, PanelMetadata


class MonitorPanel(BasePanel):
    """Panel for monitoring device values in real-time."""

    panel_metadata = PanelMetadata(
        id="my_package.panels.monitor",
        name="Device Monitor",
        description="Real-time device value monitoring",
        category="devices",
        icon="mdi.monitor-eye",
        singleton=True,
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._table: QTableWidget | None = None
        self._timer: QTimer | None = None
        self._setup_ui()
        self._start_updates()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Header
        header = QLabel("Device Monitor")
        header.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(header)

        # Table for values
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Device", "Value", "Status"])
        layout.addWidget(self._table)

    def _start_updates(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_values)
        self._timer.start(1000)  # Update every second

    def _update_values(self):
        # Update table with current device values
        pass

    def closeEvent(self, event):
        if self._timer:
            self._timer.stop()
        super().closeEvent(event)
```

### Plugin Class

```python
# my_package/plugins/monitor_plugin.py
"""Monitor panel plugin."""

from typing import TYPE_CHECKING

from lightfall.plugins.panel_plugin import PanelPlugin

if TYPE_CHECKING:
    from lightfall.ui.panels.base import BasePanel


class MonitorPanelPlugin(PanelPlugin):
    """Panel plugin providing the device monitor."""

    @property
    def name(self) -> str:
        return "monitor"

    def get_panel_class(self) -> type[BasePanel]:
        from my_package.panels.monitor_panel import MonitorPanel
        return MonitorPanel
```

## Registration

### Built-in Manifest

```python
PluginEntry(
    type_name="panel",
    name="monitor",
    import_path="my_package.plugins.monitor_plugin:MonitorPanelPlugin",
    preload=True,  # Recommended for panels
),
```

### Why Preload?

Panels should use `preload=True` so they are registered with `PanelRegistry` before the main window is created. This ensures:
- Panels appear in the View menu
- Saved layouts can restore panels
- Other plugins can reference the panel

## Panel Categories

Common categories for organizing panels:

| Category | Purpose |
|----------|---------|
| `"devices"` | Device monitoring and control |
| `"analysis"` | Data analysis tools |
| `"acquisition"` | Data acquisition controls |
| `"tools"` | General utilities |
| `"logs"` | Logging and debugging |

## Panel Metadata Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `str` | Yes | Unique identifier (e.g., "my.panels.viewer") |
| `name` | `str` | Yes | Display name in menus |
| `description` | `str` | No | Tooltip description |
| `category` | `str` | No | Menu category (default: "tools") |
| `icon` | `str` | No | Icon name (qtawesome format) |
| `singleton` | `bool` | No | Allow only one instance? (default: True) |

## Minimal Example

The simplest possible panel plugin:

```python
# plugin.py
from lightfall.plugins.panel_plugin import PanelPlugin

class SimplePanelPlugin(PanelPlugin):
    @property
    def name(self) -> str:
        return "simple"

    def get_panel_class(self):
        from my_package.panels import SimplePanel
        return SimplePanel
```

```python
# panels.py
from PySide6.QtWidgets import QLabel, QVBoxLayout
from lightfall.ui.panels.base import BasePanel, PanelMetadata

class SimplePanel(BasePanel):
    panel_metadata = PanelMetadata(
        id="my.simple",
        name="Simple Panel",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Hello, World!"))
```
