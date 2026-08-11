# Lightfall Panel Design API Reference

This document provides the full API reference for designing BasePanel subclasses for the Lightfall application.

## see-also

- [`cross_panel_patterns.md`](cross_panel_patterns.md) — recipes for panels
  that reach beyond their own widgets: dispatching prompts to the Claude
  Assistant, opening other panels, and reacting to global events (device
  connect, plan complete).

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
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.panels.registry import PanelRegistry
from lightfall.utils.logging import logger
```

## user-plugin-architecture

Lightfall has two distinct ways to create panel plugins. Understanding which to use is critical.

### User Plugins (~/lightfall/plugins/)

**This is what YOU should use.** User plugins directly subclass `BasePanel`:

```python
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.panels.registry import PanelRegistry

class MyPanel(BasePanel):
    panel_metadata = PanelMetadata(id="lightfall.panels.user.my_panel", ...)
    def _setup_ui(self) -> None: ...

# Self-register at module load time
PanelRegistry.get_instance().register(MyPanel, replace=True)
```

Key points:
- Direct `BasePanel` subclass
- Self-registers with `PanelRegistry.register()`
- Tracked by `UserPluginService` via `RegistrationTracker`
- Hot-reload supported

### Built-in Plugins (via manifest)

Built-in plugins use a **two-layer pattern** you should NOT copy:

```python
# File: lightfall/ui/panels/plugins/device_plugin.py
class DevicePanelPlugin(PanelPlugin):  # <-- Plugin wrapper
    def get_panel_class(self) -> type[BasePanel]:
        from lightfall.ui.panels.device_panel import DevicePanel
        return DevicePanel

# File: lightfall/ui/panels/device_panel.py
class DevicePanel(BasePanel):  # <-- Actual panel
    panel_metadata = PanelMetadata(...)
```

Why the two layers?
- **Lazy loading**: `PanelPlugin` is instantiated at startup, but the heavy `BasePanel` import is deferred until `get_panel_class()` is called
- **Plugin system integration**: `PanelPlugin` goes through `PluginLoader` → `PluginRegistry` → `PanelRegistry`
- **Qt independence**: The plugin system doesn't depend on Qt

### Do NOT Use PanelPlugin for User Plugins

❌ **Wrong** - Using PanelPlugin for user plugins:
```python
class MyPanelPlugin(PanelPlugin):  # DON'T DO THIS
    def get_panel_class(self):
        return MyPanel
```

✅ **Correct** - Direct BasePanel with self-registration:
```python
class MyPanel(BasePanel):  # DO THIS
    panel_metadata = PanelMetadata(...)

PanelRegistry.get_instance().register(MyPanel, replace=True)
```

`PanelPlugin` is NOT tracked by `UserPluginService` and will NOT work correctly for user plugins.

## panel-metadata

```python
@dataclass
class PanelMetadata:
    id: str                              # Unique ID, e.g., "lightfall.panels.user.my_panel"
    name: str                            # Display name in menus
    description: str = ""                # Tooltip/help text
    icon: str = ""                       # qtawesome name, e.g. "mdi6.chart-line"
                                         # (NOT a bare word like "chart-scatter" —
                                         #  unknown names render blank, no error)
    category: str = "General"            # Menu grouping: "User", "Data", "Devices", etc.
    required_permission: Permission | None = None  # Access control (usually None)
    singleton: bool = True               # Only one instance allowed
    closable: bool = True                # User can close the panel
    keywords: list[str] = field(default_factory=list)  # Search keywords

    # Docking preferences
    default_area: str = "left"           # "left" or "bottom" (gets sidebar button + title bar)
                                         # Use these for plugin panels. Do NOT use "center"
                                         # (reserved for the Logbook — center calls
                                         # setCentralWidget, so a plugin there evicts it) or
                                         # "right" (not a plugin area). A panel content area is
                                         # wrapped in a QScrollArea, so give your main widget
                                         # layout stretch=1 and pin status/footer rows to a
                                         # fixed-height container, or they balloon vertically.
    sidebar_group: str = "top"           # "top", "bottom" within sidebar
    auto_hide: bool = True               # Start in auto-hide sidebar
    sidebar_order: int = 0               # Order within group (lower = higher)

    # Startup behavior
    proactive_init: bool = True          # Eagerly instantiate in the post-startup
                                         # sweep. Set False for heavy panels that
                                         # should stay fully lazy until clicked.
    warmup_import: str = ""              # Optional module imported on a background
                                         # thread when the sweep starts, so a heavy
                                         # import chain is warm before init.
```

## lifecycle-methods

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

## keep-initialization-fast

`_setup_ui()` runs on the **main (GUI) thread**, and panels with
`proactive_init=True` (the default — see `panel-metadata`) are all instantiated
in a sweep shortly after the window appears. **Slow work in `_setup_ui()`
stalls the whole UI** — not just your panel — and a few sluggish panels add up
to a janky startup.

So: build the widgets, then return. Anything that can block — network/EPICS
connections, Tiled queries, disk reads, heavy imports — must be **offloaded to
a background thread**, with the panel showing a "loading…" state until the
result arrives. This is just good panel design generally; proactive init only
makes it more visible.

Use `lightfall.utils.threads` for this — it integrates with Qt's event loop and
marshals callbacks back to the main thread, so you never touch widgets from a
worker:

```python
from lightfall.utils import threads
from lightfall.ui.panels.base import BasePanel, PanelStatus


class MyPanel(BasePanel):
    def _setup_ui(self) -> None:
        # Fast: just lay out widgets and show a placeholder.
        self._status_label = QLabel("Connecting…")
        self._layout.addWidget(self._status_label)

        # Offload the slow part; _setup_ui returns immediately.
        threads.QThreadFuture(
            self._fetch_catalog,
            callback_slot=self._on_catalog_ready,  # delivered on the main thread
            except_slot=self._on_load_error,       # delivered on the main thread
        ).start()

    def _fetch_catalog(self) -> list[str]:
        # Runs on a BACKGROUND thread — do NOT touch Qt widgets here.
        return expensive_network_call()

    def _on_catalog_ready(self, items: list[str]) -> None:
        # Back on the main thread — safe to update widgets.
        self._populate(items)
        self.set_status(PanelStatus.SUCCESS)   # tint the sidebar icon (see panel-status-indicator)

    def _on_load_error(self, exc: Exception) -> None:
        self._status_label.setText(f"Failed: {exc}")
        self.set_status(PanelStatus.ERROR)
```

Key rules:

- **Never touch Qt widgets from the worker function.** Only the
  `callback_slot` / `finished_slot` / `except_slot` handlers (delivered via Qt
  signals) run on the main thread and may update the UI. To push an arbitrary
  call back to the GUI thread, use `threads.invoke_in_main_thread(fn, ...)`.
- The `@threads.method(callback_slot=...)` decorator is a shorthand for the
  same thing on a standalone worker; `@threads.iterator(yield_slot=...)` streams
  progress (e.g. row-by-row loads).
- Pair the background load with the status indicator: `SUCCESS` when it lands,
  `ERROR` (and a visible message) when it fails.
- If the panel's cost is mostly a **heavy import chain**, set
  `warmup_import="…"` in `PanelMetadata` so that module is imported on a
  background thread when the sweep starts — or set `proactive_init=False` to
  stay fully lazy until the user opens the panel.

## signals

```python
class BasePanel(QWidget):
    activated = Signal()           # Panel became active
    deactivated = Signal()         # Panel lost focus
    state_changed = Signal(str, object)  # key, value - state changed
    closing = Signal()             # Panel is closing
```

## state-management

```python
# Get/set panel state (survives restart if persisted)
value = self.get_state("key", default_value)
self.set_state("key", value)

# Get all state as dict
all_state = self.get_all_state()

# Restore from dict
self.restore_state(state_dict)
```

## panel-status-indicator

A panel's sidebar button can carry a **status tint** that signals health at a
glance — green when healthy, red on error, and so on. The tint recolors
whatever icon the panel already uses; it is independent of the icon itself.

### PanelStatus enum

```python
from lightfall.ui.panels.base import PanelStatus
```

| Value | Sidebar tint | Use it for |
|-------|--------------|-----------|
| `PanelStatus.UNINITIALIZED` | theme text color (default) | not yet initialized / idle |
| `PanelStatus.SUCCESS` | theme success (green) | connected, healthy, ready |
| `PanelStatus.WARNING` | theme warning (amber) | degraded, needs attention |
| `PanelStatus.ERROR` | theme error (red) | failed, disconnected, faulted |
| `PanelStatus.INFO` | theme info (blue) | informational / active state |

Tints are pulled from the **active theme** and re-applied automatically on a
theme switch — never hard-code a hex color for status.

### Setting status

```python
from lightfall.ui.panels.base import BasePanel, PanelStatus

class MyPanel(BasePanel):
    def _on_device_connected(self) -> None:
        self.set_status(PanelStatus.SUCCESS)

    def _on_device_fault(self) -> None:
        self.set_status(PanelStatus.ERROR)

    def _check(self) -> None:
        current = self.status          # -> PanelStatus (read-only property)
```

- `set_status(status)` emits `status_changed = Signal(object)`; the docking
  manager listens and re-tints the sidebar button. It is a no-op when the
  status is unchanged, so it is safe to call from a frequently-firing slot
  (e.g. an ophyd subscription callback).
- The framework drives status **automatically** around deferred/proactive
  instantiation (see `proactive_init` in `panel-metadata`): a panel that
  instantiates cleanly is marked `SUCCESS`, one that raises during creation is
  marked `ERROR`. Override it from there to reflect live runtime health.
- An explicit color passed to `set_sidebar_icon(icon_name, color)` still
  **wins** over the status tint (manual override). Pass an empty `color=""`
  to release the override and fall back to the status tint.

## mcp-introspection-api

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

### The `parameters` dict

The optional `"parameters"` entry declares the keyword arguments Claude may
pass when invoking the action. It is **advisory metadata**: it is surfaced to
Claude through `lightfall_get_panel_info` so the model knows the shape of the call,
but the framework does **not** validate it. At invoke time,
`lightfall_invoke_panel_action` passes a free-form `kwargs` object straight through
`BasePanel.invoke_action(action, **kwargs)` into your `action_<name>(**kwargs)`
method (see `ui/panels/base.py` and `claude/lightfall_core_tools.py`).

- **Keys** are parameter names. They arrive as keyword arguments to the
  `action_*` method, so they must be valid Python identifiers.
- **Values** are JSON-Schema type strings — `"string"`, `"integer"`,
  `"number"`, or `"boolean"`. For a constrained choice, use a nested object:
  `{"type": "string", "enum": ["absolute", "relative"]}`.
- **Treat every declared parameter as required.** There is no "optional" flag,
  and Claude will try to supply everything listed. To make a parameter
  optional, give the method a default and *omit* it from the dict — undeclared
  kwargs are still accepted because the handler is called with `**kwargs`.

```python
def _get_available_actions(self) -> list[dict[str, Any]]:
    actions = super()._get_available_actions()
    actions.append({
        "name": "run_scan",
        "description": "Start a scan over the named motor",
        "method": "action_run_scan",
        "parameters": {
            "motor": "string",
            "points": "integer",
            "mode": {"type": "string", "enum": ["absolute", "relative"]},
        },
    })
    return actions

def action_run_scan(
    self, motor: str, points: int, mode: str = "absolute", **kwargs
) -> bool:
    # `mode` is declared above, so Claude treats it as required. Drop it from
    # `parameters` (keeping the default here) to make it truly optional.
    ...
    return True
```

## title-bar-actions

The panel title bar hosts a small **toolbar** for high-level panel actions —
icon-only buttons sitting to the right of the title, before the window
buttons:

```
[title] ........ [your action buttons] | [expand] [redock?] [minimize]
```

This is the right home for a panel's top-level verbs (New, Refresh, Clear, a
sort/filter picker, a mode toggle). It keeps the panel body free of chrome and
gives every panel a consistent place for its controls — **prefer it over an
embedded `QToolBar`** inside the panel body.

### add_title_bar_button (recommended)

The convenience helper builds a theme-tinted qtawesome icon, wires the slot,
and adds the button in one call. It returns the created `QAction` so you can
toggle `setEnabled` / `setChecked` / `setIcon` later.

```python
def _setup_ui(self) -> None:
    ...
    # plain action button
    self.add_title_bar_button("mdi6.refresh", "Refresh", self._on_refresh)

    # checkable toggle (the slot receives the new checked state)
    self._auto_action = self.add_title_bar_button(
        "mdi6.arrow-down-bold", "Auto-scroll", self._on_auto_toggled,
        checkable=True, checked=True,
    )

    # dropdown: pass a QMenu (see the gotcha below)
    from PySide6.QtWidgets import QMenu
    sort_menu = QMenu()
    sort_menu.addAction("Newest first", lambda: self._sort("desc"))
    sort_menu.addAction("Oldest first", lambda: self._sort("asc"))
    self.add_title_bar_button("mdi6.sort", "Sort", menu=sort_menu)
```

Signature:

```python
def add_title_bar_button(
    self, icon_name: str, tooltip: str, on_triggered=None, *,
    checkable: bool = False, checked: bool = False, menu=None,
) -> QAction
```

> Buttons are **icon-only** (20×20). The tooltip is the only label, so always
> pass a clear one. Use `mdi6.*` icon names.

> **Menu gotcha:** pass the `QMenu` via `menu=` and do **not**
> `setParent(self)` on it. Reparenting a popup menu to a normal widget clears
> its `Qt.Popup` window flag, so it renders inline (filling the panel) instead
> of popping from the button. The helper keeps a reference to the menu for you.

### add_title_bar_action (raw QAction)

When you already have a `QAction` (e.g. one shared with a menu or keyboard
shortcut), add it directly:

```python
from PySide6.QtGui import QAction
import qtawesome as qta

self._new_plan_action = QAction(
    qta.icon("mdi6.file-plus-outline"), "New Plan", self
)
self._new_plan_action.triggered.connect(self._on_new_plan)
self.add_title_bar_action(self._new_plan_action)
```

`title_bar_actions` returns the current list, and `add_title_bar_action` emits
`title_bar_actions_changed = Signal()` — so actions added *after* the title bar
is first built still appear. `add_title_bar_button` is just a thin wrapper that
builds the themed `QAction` and calls `add_title_bar_action`.

> A title bar button is also a natural place to kick off an *AI-mediated*
> procedure — wire `on_triggered` to the dispatch pattern in
> `triggering-the-claude-assistant` below.

## triggering-the-claude-assistant

Some panels want a button that drives an *AI-mediated* procedure — run a skill,
summarize the current state, draft a logbook note — rather than performing the
work themselves. The pattern is to dispatch a prompt to the singleton Claude
Assistant panel (`lightfall.panels.claude`), which then runs the relevant skill in
the chat the user can already see.

Use this when the action is open-ended or best handled by a skill (and when you
want the conversation visible to the operator). For deterministic, fully
specified operations, expose an MCP action (see `mcp-introspection-api`) or do
the work directly in the panel instead.

### Recommended: use `SkillTriggerButton`

`SkillTriggerButton` (`lightfall.ui.widgets.skill_trigger_button`) packages the
whole flow — get-or-open the Claude panel, guard on a busy agent, send the
prompt, report status — into one drop-in widget. It emits `dispatched(str)`
on success so you can chain follow-up behavior.

```python
from lightfall.ui.widgets.skill_trigger_button import SkillTriggerButton

btn = SkillTriggerButton(skill_name="Beam Alignment", prompt="Run the beam alignment skill.")
self._layout.addWidget(btn)
```

### Manual pattern (for advanced cases)

When you need to collect Claude's reply (as `LogbookPanel` does) rather than
fire-and-forget, drive the bridge yourself. This mirrors
`LogbookPanel._get_claude_panel` / `_send_to_claude`:

```python
from lightfall.ui.panels.registry import PanelRegistry

registry = PanelRegistry.get_instance()
panel = registry.create("lightfall.panels.claude")     # get-or-open the singleton
widget = getattr(panel, "_claude_widget", None)
if widget is None or not hasattr(widget, "agent"):
    return
agent = widget.agent
if agent.is_busy():                                 # don't interrupt a running query
    return
agent.message_received.connect(self._on_claude_message)   # optional: collect reply
agent.query_completed.connect(self._on_claude_complete)
panel.action_send_message(prompt)                   # also shows in the chat UI
```

Agent signals worth knowing:

- `is_busy() -> bool` — `True` while a query is queued or running. Always guard
  on it before sending.
- `message_received(str)` — emitted per response chunk. Connect to accumulate
  Claude's reply.
- `query_completed()` — emitted once when the query finishes. Disconnect your
  handlers here (and clear any pending state).

> **Trigger-prompt phrasing.** When writing the prompt, include the target
> skill's name verbatim and at least one phrase from its "Use this skill
> when…" description in `SKILL.md`. This maximizes the match-rate over
> conversational rewordings.

> **Writing a skill that's easy to trigger from a panel.** If your skill is
> likely to be triggered from a panel button (not just a chat message), make
> it easy:
>
> - Write the "Use this skill when…" description so it contains a short,
>   action-oriented phrase a panel can put verbatim in its trigger prompt.
>   Bad: "Use when the operator needs help with X." Good: "Use when the user
>   asks to run X, perform an X scan, or start an X procedure."
> - Avoid requiring information the panel can't supply. If the skill needs
>   operator confirmation of a manual checklist, surface that as the skill's
>   first interactive step (so the panel can fire-and-forget the prompt and
>   let the skill handle the dialog).

## self-registration-pattern

User plugins MUST self-register at module load time:

```python
# At the END of your plugin file:
PanelRegistry.get_instance().register(MyPanel, replace=True)
```

The `replace=True` is REQUIRED for hot-reload support.

## complete-example

```python
"""My custom panel for Lightfall."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout, QWidget,
)
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.ui.panels.registry import PanelRegistry
from lightfall.utils.logging import logger


class MyCustomPanel(BasePanel):
    """A custom panel that shows a greeting."""

    panel_metadata = PanelMetadata(
        id="lightfall.panels.user.my_custom",
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

## file-conventions

User plugins are stored in: `~/lightfall/plugins/`

Naming conventions:
- Filename: lowercase with underscores (e.g., `my_panel.py`)
- Panel ID: `lightfall.panels.user.<name>` (e.g., `lightfall.panels.user.my_panel`)
- Class name: PascalCase ending in Panel (e.g., `MyPanel`)

## qt-layout-tips

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

## common-widgets

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

## compact-motor-widget

For simple motor controls in a panel, use `CompactMotorWidget` — a single-row
widget with status dot, readback, jog/abs toggle, setpoint entry, go, and stop.
It handles ophyd subscription, units, and motion state internally.

```python
from lightfall.ui.widgets.compact_motor import CompactMotorWidget
from lightfall.devices import DeviceCatalog

catalog = DeviceCatalog.get_instance()
info = catalog.get_device_by_name("sample_x")
widget = CompactMotorWidget(device_info=info, ophyd_obj=info.ophyd_device)
self._layout.addWidget(widget)
```

The `ophyd_obj` may be `None` initially; call `widget.set_motor(...)` once
the device finishes connecting (e.g. from `DeviceCatalog.device_connected`).

## lightfall-services

```python
# Toast notifications
from lightfall.ui.toast import ToastManager
toast = ToastManager.get_instance()
toast.success("Title", "Message")
toast.error("Title", "Message")
```

> **Prefer Qt palette role names** (`palette(text)`, `palette(highlight)`,
> `palette(mid)`, `palette(window)`) over hex codes in stylesheets — they
> auto-track theme switches and stay correct in both light and dark mode
> without an explicit re-style.

```python
# Theme awareness
from lightfall.ui.theme import ThemeManager
theme_mgr = ThemeManager.get_instance()
is_dark = theme_mgr.is_dark

# Preferences
from lightfall.ui.preferences.manager import PreferencesManager
prefs = PreferencesManager.get_instance()
value = prefs.get("key", default)
prefs.set("key", value)

# Device catalog
from lightfall.devices import DeviceCatalog
from lightfall.devices.model import DeviceCategory

catalog = DeviceCatalog.get_instance()
# list_devices returns DeviceInfo objects
motor_infos = catalog.list_devices(category=DeviceCategory.MOTOR)
# Access ophyd device via .ophyd_device property
motors = [info.ophyd_device for info in motor_infos if info.ophyd_device]
# Or get ophyd device directly by name
motor = catalog.get_ophyd_device("sample_x")
```

## device-catalog-api

The DeviceCatalog provides device discovery and access. It manages DeviceInfo
objects which wrap ophyd devices with metadata and state tracking.

### Two-Layer Architecture

- **DeviceInfo**: Metadata wrapper with name, category, state, etc.
- **ophyd device**: The actual hardware interface (accessed via `.ophyd_device`)

### Listing and Searching Devices

```python
from lightfall.devices import DeviceCatalog
from lightfall.devices.model import DeviceCategory, DeviceInfo

catalog = DeviceCatalog.get_instance()

# List with filters (returns list[DeviceInfo])
devices = catalog.list_devices(category=DeviceCategory.MOTOR, beamline="BL1", active_only=True)
devices = catalog.search_devices("sample")  # Search by name/description/tags
devices = catalog.get_all_devices()         # Get all devices
```

### Getting a Single Device

```python
# By name (returns DeviceInfo | None)
device_info = catalog.get_device_by_name("sample_x")

# By EPICS prefix
device_info = catalog.get_device_by_prefix("IOC:m1")
```

### Accessing the Ophyd Device

```python
# Direct access (returns ophyd device or None)
ophyd_dev = catalog.get_ophyd_device("sample_x")

# Via DeviceInfo (may be None if not connected)
ophyd_dev = device_info.ophyd_device

# Control the device
if ophyd_dev:
    position = ophyd_dev.position       # Read position
    ophyd_dev.set(10.0).wait()          # Move and wait
    ophyd_dev.stop()                    # Stop motion
```

### DeviceInfo Properties

```python
device_info.id           # UUID - unique identifier
device_info.name         # str - human-readable name
device_info.category     # DeviceCategory enum
device_info.description  # str - device description
device_info.prefix       # str - EPICS PV prefix
device_info.beamline     # str | None - beamline assignment
device_info.active       # bool - is device active
device_info.state        # DeviceState - current state (position, status, alarms)
device_info.ophyd_device # The actual ophyd device instance (may be None)
```

### DeviceCategory Enum

Available categories (independent vs dependent variable classification):
- `DeviceCategory.MOTOR` - Physical read/write (motors, positioners, slits) — independent variable
- `DeviceCategory.DETECTOR` - Measures something (detectors, cameras, sensors, diodes, signals) — dependent variable
- `DeviceCategory.CONTROLLER` - Non-physical read/write (temperature controllers, delay generators, power supplies) — independent variable, catch-all default
