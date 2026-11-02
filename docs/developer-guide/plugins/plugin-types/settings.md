# SettingsPlugin

Settings plugins add pages to the Preferences dialog.

## Purpose

Use `SettingsPlugin` when you want to:
- Add configurable options for your feature
- Let users, consoles, branches or beamlines set them (scope-aware)
- Apply settings at startup (`on_loaded()`)

## Base Class

```python
from lightfall.plugins.settings_plugin import SettingsPlugin
```

## Class Attributes

| Attribute | Value | Description |
|-----------|-------|-------------|
| `type_name` | `"settings"` | Plugin type identifier |
| `is_singleton` | `True` | One instance per plugin |
| `scope_aware` | `True` | Set `False` for a custom page (see below) |

## Two kinds of page

A page is either:

- a **form page** (the default): you describe its settings with `sections()`
  and the dialog builds the controls. The page header picks the scope being
  edited (This console, your user settings, a branch or beamline); each scope
  shows only its own values, and OK/Apply writes only the fields you changed,
  at that scope. Use this for anything stored in `PreferencesManager`.
- a **custom page**: `scope_aware = False` and `create_widget()`. The page
  builds its own widget and applies changes immediately; the dialog never
  loads, validates or saves it. Use it only for pages that are not settings
  (an editor, a list with its own buttons).

A plugin that is neither (it still has the removed `load_settings()` /
`save_settings()` page API) shows a placeholder: "*Name* uses an older
settings API and can't be shown. Update the plugin."

## Form pages

```python
from lightfall_settings import SettingSpec

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.form.schema import (
    Action, Info, Integer, Path, Section, SettingField, Text,
)


class ScannerSettingsPlugin(SettingsPlugin):
    @property
    def name(self) -> str:
        return "scanner"

    def specs(self):
        return (
            SettingSpec(key="scanner.host", type=str, scopes={"beamline", "branch"}),
            SettingSpec(key="scanner.points", type=int, default=50, scopes={"user"}),
            SettingSpec(key="scanner.log_dir", type=str, scopes={"user"}),
        )

    def console_keys(self):
        return ("scanner.log_dir",)  # this console's path: shown at This console only

    def sections(self):
        return [
            Section("Scanner", [
                SettingField("scanner.host", Text(blank_is_unset=True), label="Host"),
                SettingField("scanner.points", Integer(minimum=1, maximum=10_000),
                             label="Points", unit="pts"),
                SettingField("scanner.log_dir", Path(mode="dir"), label="Log folder"),
                Action("Test connection", run=self._test, scopes=frozenset({"local"})),
                Info("Changes apply to the next scan."),
            ]),
        ]

    def _test(self, values) -> str:  # runs on a worker thread
        host = values.effective("scanner.host")
        return f"{host} answered" if host else "No host set"
```

### specs()

The `lightfall_settings.SettingSpec`s for
keys nothing else declares: `type`, `default` and the scope kinds that may set
the key (`"facility"`, `"beamline"`, `"branch"`, `"user"`). A scope only offers
the fields its kind allows. Called at registration time, so it must not depend
on instance state.

### console_keys()

Keys that belong to this console only (its devices, paths, daemons). They are
shown and saved only at "This console" and never written to a server scope.
Values an older version of the page stored in the user's settings are copied
to This console once, at the user's first login after the update.

### sections()

A list of `Section(title, items, description="")`. Items are shown in order:

| Item | What it is |
|------|------------|
| `SettingField(key, editor, label, description, placeholder, unit, restart_required, visible_when, enabled_when, validate, companions)` | One setting. `validate(value, values)` returns an error message or `None`. |
| `Action(label, run, enabled_when, scopes, visible_when, run_on_show, refresh_on)` | A button. `run(values)` runs on a worker thread and returns the status text; an exception shows as an error. Actions write nothing. |
| `Info(text, scopes, field, visible_when)` | A muted line; `text` may be a callable of the pending values. |
| `Embed(factory, scopes)` | A plain widget that is not a preference; `factory(parent)` is called each time the scope changes. |

`scopes` limits an item to scope kinds (`"local"` is This console);
`visible_when` / `enabled_when` are predicates on the pending values.

Editors (all in `lightfall.ui.preferences.form.schema`): `Toggle()`,
`Choice(options)`, `Integer(minimum, maximum, step)`,
`Number(minimum, maximum, step)`, `Text(blank_is_unset)`, `Url(schemes, require_host, blank_is_unset)`, `Secret()`,
`Path(mode="file" | "dir", filter, blank_is_unset)`,
`NumberList(minimum)` (comma-separated numbers, a `list[float]`),
`Catalog(items, columns)` and `Mapping(items, value, columns)` for item-merged
maps, and `Custom(factory)` for anything else (a `FieldEditor` subclass from
`lightfall.ui.preferences.form.editors`). Every editor can be *Not set*
(`None`), which is distinct from `False`, `0` and `""`.

### Hooks

| Hook | Called |
|------|--------|
| `validate_values(values)` | Before saving, after the field validators; return a list of error messages. |
| `preview(values)` | After every edit, for a live preview (e.g. a theme). Undo it in `revert_preview()`, called on Cancel. |
| `after_save(scope, changed)` | After the changed values were written at `scope`. Apply them to running services here. |
| `restart_reason(scope, changed)` | After `after_save`; a non-empty reason makes the dialog offer a restart. |
| `on_loaded()` | Once, when the plugin is loaded (before the main window for `preload=True` entries). |

`values` is a `FormValues`: `values.scope`, `values.get(key)` (the pending
value at this scope, `None` = not set), `values.effective(key, default)` (what
would apply) and `values.is_dirty(key)`.

Widgets on a form page do not act on running services while the user edits;
apply saved values in `after_save()`. For values that can change while the
app runs (another console, a beamline save, login), subscribe in
`on_loaded()`:

```python
def on_loaded(self) -> None:
    PreferencesManager.get_instance().subscribe("scanner.host", self._on_host)
```

## Custom pages

```python
class NotesPagePlugin(SettingsPlugin):
    scope_aware = False

    @property
    def name(self) -> str:
        return "notes"

    def create_widget(self, parent=None):
        return NotesEditor(parent)  # saves its own changes immediately
```

## Optional properties

| Property | Default | Description |
|----------|---------|-------------|
| `display_name` | title-cased `name` | Sidebar label |
| `icon` | `None` | Sidebar `QIcon` |
| `category` | `"general"` | Sidebar group (`general`, `appearance`, `advanced`, `plugins`, ...) |
| `priority` | `100` | Order within the category (lower first) |

## Lifecycle

1. The plugin is instantiated on load; its specs and console keys are registered
2. `on_loaded()` is called (for preload plugins, before the main window)
3. When the Preferences dialog opens, a form page is built from `sections()`
   (a custom page's `create_widget()` is called)
4. As the user edits a form page, `preview(values)` is called
5. On OK/Apply: field validators and `validate_values()` run, the changed
   values are written at the selected scope, then `after_save()` and
   `restart_reason()` are called
6. On Cancel: `revert_preview()` undoes any preview

## Registration

### Built-in Manifest

```python
PluginEntry(
    type_name="settings",
    name="scanner",
    import_path="my_package.settings:ScannerSettingsPlugin",
),
```

### With Preload

For settings that must apply before the main window (e.g., theme):

```python
PluginEntry(
    type_name="settings",
    name="appearance",
    import_path="lightfall.ui.preferences.builtin:AppearanceSettingsPlugin",
    preload=True,  # Load before main window
),
```

## Reading settings at runtime

```python
from lightfall.ui.preferences.manager import PreferencesManager

prefs = PreferencesManager.get_instance()
points = prefs.get("scanner.points", 50)  # resolved through the scope chain
```

See [Settings Layering](../../settings-layering.md) for how scopes resolve.
