# Panels

The panel framework in `lightfall.ui.panels.base`. Panels are the dockable
units of the Lightfall UI: each is a `QWidget` subclass with declarative
metadata, a standard lifecycle, title-bar extension points, and an
introspection surface that lets the embedded agent inspect and operate the
panel.

```python
from lightfall.ui.panels.base import BasePanel, PanelMetadata, PanelStatus
```

## BasePanel

`BasePanel` extends `QWidget`. A minimal panel defines class-level
`panel_metadata` and builds its UI in `_setup_ui()`:

```python
class MyPanel(BasePanel):
    panel_metadata = PanelMetadata(
        id="lightfall.panels.my_panel",
        name="My Panel",
        description="Does something useful",
    )

    def _setup_ui(self):
        label = QLabel("Hello!")
        self._layout.addWidget(label)
```

Panel content lives inside a built-in vertical `QScrollArea`: add widgets
to `self._layout` (the inner container's layout) and the scroll area stays
transparent to the subclass — content that exceeds the panel's height
scrolls instead of clipping. Horizontal scrolling is disabled; size content
to fit the available width.

### PanelMetadata

`panel_metadata` is a class-level `PanelMetadata` dataclass consumed by
`PanelRegistry` (discovery and instantiation), the agent's MCP tools
(introspection), and the docking manager (placement).

| Field | Type / default | Description |
|-------|----------------|-------------|
| `id` | `str` | Unique panel identifier (e.g. `"lightfall.panels.device"`). Becomes the widget's `objectName`. |
| `name` | `str` | Human-readable panel name. |
| `description` | `str = ""` | Detailed description of the panel's purpose. |
| `icon` | `str = ""` | Icon name or path. |
| `category` | `str = "General"` | Grouping category (e.g. `"Device"`, `"Data"`, `"Admin"`). |
| `required_permission` | `Permission \| None = None` | Permission needed to access the panel; `None` = unrestricted. |
| `singleton` | `bool = True` | Whether only one instance can exist. |
| `closable` | `bool = True` | Whether the user can close the panel. |
| `keywords` | `list[str] = []` | Search keywords (used by `matches_search()`). |
| `default_area` | `str = "left"` | Default dock area: `"left"`, `"right"`, `"bottom"`, `"center"`. |
| `sidebar_group` | `str = "top"` | Sidebar group within the area: `"top"` or `"bottom"`. |
| `auto_hide` | `bool = True` | Start in auto-hide sidebar mode. |
| `sidebar_order` | `int = 0` | Order within the sidebar group (lower = higher). |
| `proactive_init` | `bool = True` | Eagerly instantiate during the post-startup proactive-init sweep. Set `False` for heavy panels that should stay fully lazy. |
| `warmup_import` | `str = ""` | Optional module name imported in a background thread when the proactive-init sweep starts, so a heavy import chain (e.g. `"lightfall.claude"`) is already in `sys.modules` when the panel initializes. |

### Lifecycle

| Hook / method | Called when | Notes |
|---------------|-------------|-------|
| `_setup_ui()` | During `__init__`, after the layout exists | Build the UI here. |
| `activate()` / `_on_activated()` | Panel becomes the active/focused panel | `activate()` emits `activated` and calls the `_on_activated()` subclass hook. |
| `deactivate()` / `_on_deactivated()` | Panel loses focus | Mirror of activate. |
| `can_close(force=False)` | Before close | Returns `panel_metadata.closable` by default; override for unsaved-work confirmation. `force=True` (application shutdown) bypasses the metadata flag. |
| `closeEvent(event)` / `_on_closing()` | Qt close event | Emits `closing` then calls `_on_closing()` if `can_close()` allows. |

Signals: `activated`, `deactivated`, `state_changed(key, value)`,
`closing`, `icon_changed(icon_name, color)`, `status_changed(PanelStatus)`,
`title_bar_actions_changed`.

### Status and sidebar icon

`PanelStatus` (enum: `UNINITIALIZED`, `SUCCESS`, `WARNING`, `ERROR`,
`INFO`) drives the sidebar icon tint. Call `set_status(status)` to update
it (emits `status_changed`); read it back via the `status` property.
`set_sidebar_icon(icon_name, color)` changes the sidebar icon and/or color
at runtime (empty strings keep the current icon / reset to the theme
default).

### Title-bar injection

Panels can place controls in their title bar:

| Method | Description |
|--------|-------------|
| `add_title_bar_action(action)` | Add a `QAction` rendered as an icon-only button. Give the action an icon; its text/tooltip becomes the button tooltip. |
| `add_title_bar_button(icon_name, tooltip, on_triggered=None, *, checkable=False, checked=False, menu=None)` | Convenience that builds a themed qtawesome `QAction` and adds it. `menu` (a `QMenu`) makes the button open a popup — used for sort/filter pickers. Returns the created `QAction`. |
| `add_title_bar_widget(widget)` | Place an arbitrary caller-owned widget (e.g. a status spinner doubling as a toggle) in the title bar. |
| `title_bar_actions` / `title_bar_widgets` | Properties returning copies of the registered lists. |

Title-bar actions can be registered during `_setup_ui()` — the internal
lists exist before it runs. Each addition emits `title_bar_actions_changed`
so the docking manager rebuilds the title bar.

> 🖼️ **Image placeholder** — *Screenshot: a panel title bar showing injected icon buttons (e.g. the Plans panel with sort/filter buttons) with a tooltip visible.*

### State management

`set_state(key, value)` / `get_state(key, default=None)` store arbitrary
panel state; `set_state` emits `state_changed` when the value changes.
`get_all_state()` returns the full dict and `restore_state(state)` replays
one — used for session persistence.

### Permissions

If `panel_metadata.required_permission` is set, `check_access(user)`
(classmethod) consults the `SessionManager` policy engine; otherwise it
returns `True`. The registry uses this to filter panels per user.

### Introspection and actions

This is the surface the embedded agent uses to read and operate panels:

- `get_introspection_data()` returns a dict with `metadata`, `is_active`,
  `is_visible`, `is_enabled`, `state`, `geometry`, a recursive `widgets`
  tree (class, object name, visibility, text/value where available, depth
  ≤ 3), and `actions`. Subclasses extend it by overriding
  `_get_specific_introspection_data()`.
- `_get_available_actions()` lists invocable actions; the base returns
  `activate` and `close`. Override to advertise panel-specific actions.
- `get_class_introspection_data()` (classmethod) returns class-level data
  for panel discovery without instantiation.
- `invoke_action(action_name, **kwargs)` invokes an action by name.
  Built-ins: `"activate"`, `"close"`, and `"set_state"` (kwargs `key`,
  `value`). Any other name is dispatched to a method named
  `action_<name>` on the panel; unknown names raise `ValueError`.

The `action_<name>` convention is how panels expose custom operations: a
method `def action_clear_log(self):` is invocable as
`invoke_action("clear_log")`. Advertise such actions in
`_get_available_actions()` so the agent can discover them.

## Registration

Panels reach the UI through `PanelRegistry`
(`lightfall.ui.panels.registry.PanelRegistry`), a singleton
(`get_instance()`) that maps panel IDs to classes, manages singleton
instances, and filters by permission. Three registration routes exist:

1. **PanelPlugin** (manifest-based packages) — see
   [Plugin Types](plugins.md); the loader calls
   `PanelRegistry.register(panel_class, replace=True)`.
2. **Entry points** — `[project.entry-points."lightfall.panels"]` mapping a
   name to a `BasePanel` subclass.
3. **Direct registration** (user plugin files) —
   `PanelRegistry.get_instance().register(MyPanel, replace=True)` at module
   scope.

Instances are created via `PanelRegistry.create(panel_id)`.

## Class reference

```{eval-rst}
.. autoclass:: lightfall.ui.panels.base.PanelMetadata
   :members:
   :show-inheritance:
```

```{eval-rst}
.. autoclass:: lightfall.ui.panels.base.PanelStatus
   :members:
   :show-inheritance:
```

```{eval-rst}
.. autoclass:: lightfall.ui.panels.base.BasePanel
   :members:
   :show-inheritance:
   :member-order: bysource
```

```{eval-rst}
.. autoclass:: lightfall.ui.panels.registry.PanelRegistry
   :members:
   :show-inheritance:
```
