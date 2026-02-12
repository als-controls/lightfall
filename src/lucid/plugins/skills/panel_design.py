"""Panel design skill plugin.

Provides Claude with expertise for designing BasePanel subclasses
for the LUCID application. This skill teaches the full panel API
including metadata, lifecycle, state management, and self-registration.
"""

from __future__ import annotations

from typing import Any

from lucid.plugins.skill_plugin import SkillPlugin


class PanelDesignSkill(SkillPlugin):
    """Skill for designing LUCID panel plugins.

    This skill provides Claude with deep expertise for:
    - BasePanel lifecycle and API
    - PanelMetadata configuration
    - State management and introspection
    - Self-registration pattern for user plugins
    - Qt/PySide6 component patterns
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this skill."""
        return "panel_design"

    @property
    def display_name(self) -> str:
        """Return human-readable display name."""
        return "Panel Design"

    @property
    def description(self) -> str:
        """Return description of this skill's capabilities."""
        return "Expertise in designing LUCID panel plugins with self-registration"

    @property
    def category(self) -> str:
        """Return category for grouping in settings UI."""
        return "development"

    @property
    def enabled_by_default(self) -> bool:
        """Return whether this skill is enabled by default."""
        return True

    @property
    def priority(self) -> int:
        """Return priority (lower = higher in prompt order)."""
        return 20

    def get_system_prompt(self) -> str:
        """Return the system prompt snippet for panel design expertise."""
        return '''
## LUCID Panel Design Expertise

You are an expert at designing panel plugins for LUCID. Panels are Qt widgets
that provide UI functionality and can be opened from the View menu.

---

## Required Imports

```python
from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QCheckBox, QGroupBox, QTableWidget, QTableWidgetItem,
    QSplitter, QScrollArea, QFrame, QSizePolicy,
)
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.panels.registry import PanelRegistry
from lucid.utils.logging import logger
```

---

## PanelMetadata Fields

```python
@dataclass
class PanelMetadata:
    id: str                              # Unique ID, e.g., "lucid.panels.user.my_panel"
    name: str                            # Display name in menus
    description: str = ""                # Tooltip/help text
    icon: str = ""                       # Icon name (mdi.icon-name or path)
    category: str = "General"            # Menu grouping: "User", "Data", "Devices", etc.
    required_permission: Permission | None = None  # Access control (usually None)
    singleton: bool = True               # Only one instance allowed
    closable: bool = True                # User can close the panel
    keywords: list[str] = field(default_factory=list)  # Search keywords

    # Docking preferences
    default_area: str = "left"           # "left", "right", "bottom", "center"
    sidebar_group: str = "top"           # "top", "bottom" within area
    auto_hide: bool = True               # Start in auto-hide sidebar
    sidebar_order: int = 0               # Order within group (lower = higher)
```

---

## BasePanel Lifecycle Methods

### Required: `_setup_ui()`

Build your UI here. Called during `__init__` after layout is created.

```python
def _setup_ui(self) -> None:
    """Build the panel's user interface."""
    # self._layout is already a QVBoxLayout with no margins
    self.label = QLabel("Hello!")
    self._layout.addWidget(self.label)
```

### Optional Hooks

```python
def _on_activated(self) -> None:
    """Called when panel becomes active/focused."""
    pass

def _on_deactivated(self) -> None:
    """Called when panel loses focus."""
    pass

def _on_closing(self) -> None:
    """Called when panel is about to close."""
    pass

def can_close(self, force: bool = False) -> bool:
    """Return False to prevent closing (e.g., unsaved work)."""
    return True
```

---

## Signals

```python
class BasePanel(QWidget):
    activated = Signal()           # Panel became active
    deactivated = Signal()         # Panel lost focus
    state_changed = Signal(str, object)  # key, value - state changed
    closing = Signal()             # Panel is closing
```

---

## State Management

```python
# Get/set panel state (survives restart if persisted)
value = self.get_state("key", default_value)
self.set_state("key", value)

# Get all state as dict
all_state = self.get_all_state()

# Restore from dict
self.restore_state(state_dict)
```

---

## Introspection API for MCP Tools

Override these to expose panel-specific data and actions to Claude:

```python
def _get_specific_introspection_data(self) -> dict[str, Any]:
    """Return panel-specific data for MCP tools."""
    return {
        "current_value": self._value,
        "item_count": len(self._items),
    }

def _get_available_actions(self) -> list[dict[str, Any]]:
    """Return actions Claude can invoke."""
    actions = super()._get_available_actions()
    actions.extend([
        {
            "name": "refresh",
            "description": "Refresh the data",
            "method": "action_refresh",
        },
        {
            "name": "set_filter",
            "description": "Set the filter text",
            "method": "action_set_filter",
            "parameters": {"filter_text": "string"},
        },
    ])
    return actions

def action_refresh(self, **kwargs) -> bool:
    """Custom action handler for 'refresh'."""
    self._load_data()
    return True

def action_set_filter(self, filter_text: str = "", **kwargs) -> bool:
    """Custom action handler for 'set_filter'."""
    self._filter_input.setText(filter_text)
    return True
```

---

## Self-Registration Pattern

User plugins MUST self-register at module load time:

```python
# At the END of your plugin file:
PanelRegistry.get_instance().register(MyPanel, replace=True)
```

The `replace=True` is REQUIRED for hot-reload support.

---

## Complete Working Example

```python
"""My custom panel for LUCID."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout, QWidget,
)
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.panels.registry import PanelRegistry
from lucid.utils.logging import logger


class MyCustomPanel(BasePanel):
    """A custom panel that shows a greeting."""

    panel_metadata = PanelMetadata(
        id="lucid.panels.user.my_custom",
        name="My Custom Panel",
        description="A simple panel showing a personalized greeting",
        icon="mdi.hand-wave",
        category="User",
        keywords=["greeting", "hello", "custom"],
    )

    # Custom signal
    name_changed = Signal(str)

    def _setup_ui(self) -> None:
        """Build the panel UI."""
        # Name input row
        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("Name:"))
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("Enter your name")
        self._name_input.textChanged.connect(self._update_greeting)
        input_row.addWidget(self._name_input)

        # Greet button
        self._greet_btn = QPushButton("Greet")
        self._greet_btn.clicked.connect(self._on_greet_clicked)
        input_row.addWidget(self._greet_btn)

        # Greeting label
        self._greeting_label = QLabel("Hello!")
        self._greeting_label.setStyleSheet("font-size: 18px; padding: 10px;")

        # Add to layout
        self._layout.addLayout(input_row)
        self._layout.addWidget(self._greeting_label)
        self._layout.addStretch()

        # Restore state
        saved_name = self.get_state("name", "")
        if saved_name:
            self._name_input.setText(saved_name)

    def _update_greeting(self, name: str) -> None:
        """Update greeting when name changes."""
        if name:
            self._greeting_label.setText(f"Hello, {name}!")
        else:
            self._greeting_label.setText("Hello!")
        self.set_state("name", name)
        self.name_changed.emit(name)

    def _on_greet_clicked(self) -> None:
        """Handle greet button click."""
        name = self._name_input.text() or "World"
        logger.info("Greeting: {}", name)

    def _on_activated(self) -> None:
        """Focus the name input when panel is activated."""
        self._name_input.setFocus()

    # MCP introspection

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Return panel-specific data."""
        return {
            "current_name": self._name_input.text(),
            "greeting_text": self._greeting_label.text(),
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Return available actions."""
        actions = super()._get_available_actions()
        actions.extend([
            {
                "name": "set_name",
                "description": "Set the name for greeting",
                "method": "action_set_name",
                "parameters": {"name": "string"},
            },
            {
                "name": "greet",
                "description": "Trigger the greet button",
                "method": "action_greet",
            },
        ])
        return actions

    def action_set_name(self, name: str = "", **kwargs) -> bool:
        """MCP action: Set the name."""
        self._name_input.setText(name)
        return True

    def action_greet(self, **kwargs) -> bool:
        """MCP action: Click the greet button."""
        self._on_greet_clicked()
        return True


# Self-register with the panel registry (REQUIRED for user plugins)
PanelRegistry.get_instance().register(MyCustomPanel, replace=True)
```

---

## File Conventions

User plugins are stored in: `~/lucid/plugins/`

Naming conventions:
- Filename: lowercase with underscores (e.g., `my_panel.py`)
- Panel ID: `lucid.panels.user.<name>` (e.g., `lucid.panels.user.my_panel`)
- Class name: PascalCase ending in Panel (e.g., `MyPanel`)

---

## Qt Layout Tips

```python
# Vertical layout (stack widgets top to bottom)
layout = QVBoxLayout()

# Horizontal layout (stack widgets left to right)
layout = QHBoxLayout()

# Add stretch to push widgets together
layout.addStretch()

# Set margins (left, top, right, bottom)
layout.setContentsMargins(10, 10, 10, 10)

# Set spacing between widgets
layout.setSpacing(5)

# Group widgets in a titled box
group = QGroupBox("Settings")
group_layout = QVBoxLayout(group)
group_layout.addWidget(widget)

# Splitter for resizable sections
splitter = QSplitter(Qt.Orientation.Horizontal)
splitter.addWidget(left_panel)
splitter.addWidget(right_panel)

# Scrollable area
scroll = QScrollArea()
scroll.setWidgetResizable(True)
scroll.setWidget(content_widget)
```

---

## Common Widgets

```python
# Label with text
label = QLabel("Text")

# Single-line text input
line_edit = QLineEdit()
line_edit.setPlaceholderText("Hint...")
line_edit.textChanged.connect(handler)

# Integer spinner
spin = QSpinBox()
spin.setRange(0, 100)
spin.setValue(10)
spin.valueChanged.connect(handler)

# Float spinner
double_spin = QDoubleSpinBox()
double_spin.setRange(0.0, 10.0)
double_spin.setDecimals(3)
double_spin.setValue(1.0)

# Dropdown
combo = QComboBox()
combo.addItems(["Option 1", "Option 2"])
combo.currentTextChanged.connect(handler)

# Checkbox
check = QCheckBox("Enable feature")
check.setChecked(True)
check.stateChanged.connect(handler)

# Button
btn = QPushButton("Click Me")
btn.clicked.connect(handler)
```

---

## Using LUCID Services

```python
# Toast notifications
from lucid.ui.toast import ToastManager
toast = ToastManager.get_instance()
toast.success("Title", "Message")
toast.error("Title", "Message")

# Theme awareness
from lucid.ui.theme import ThemeManager
theme_mgr = ThemeManager.get_instance()
is_dark = theme_mgr.is_dark

# Preferences
from lucid.ui.preferences.manager import PreferencesManager
prefs = PreferencesManager.get_instance()
value = prefs.get("key", default)
prefs.set("key", value)

# Device catalog
from lucid.devices import DeviceCatalog
catalog = DeviceCatalog.get_instance()
devices = catalog.get_devices_by_category("motor")
```
'''

    def create_tools(self) -> list[Any]:
        """Return tools provided by this skill."""
        # This skill provides guidance only, no additional tools
        return []
