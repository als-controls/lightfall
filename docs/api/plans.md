# Plans

Plan authoring surfaces: the plan registry, parameter introspection, the
`Annotated` UI vocabulary that drives automatic form generation, user plan
files, and embedded plan UIs.

```python
from lightfall.acquire.plans import PlanRegistry, PlanInfo, ParameterInfo, get_registry
from lightfall.ui.annotations import Unit, Decimals, Range, DeviceFilter
from lightfall.acquire.plan_ui import PlanUI, PlanState, plan_with_ui
```

## How plans get registered

| Route | Mechanism |
|-------|-----------|
| **User plan files** | Drop a `.py` file in `~/lightfall/plans/`; `UserPlanService` loads it, watches it, and registers it under the `"user"` category. The primary route for beamline-local plans. |
| **Programmatic** | `PlanRegistry.register(name, func, category)` or the `@registry.register_decorator(...)` decorator. |
| **PlanPlugin** | The `PlanPlugin` class exists (see [Plugin Types](plugins.md)), but the `"plan"` manifest type is not registered in the current startup sequence, so manifest entries of this type are skipped. |

The default registry does *not* register raw Bluesky builtins (`bp.scan`,
`bp.grid_scan`, ...) — their `*args` signatures cannot generate useful
forms. Instead, typed wrapper plans in
`lightfall.acquire.plans.lightfall_plans` (e.g. `scan_1d`, `rel_scan_1d`)
provide the same functionality with annotated signatures. Raw `bp.*` plans
remain accessible through the agent's plan-code tool and the IPython
console.

## User plans (`~/lightfall/plans/`)

Each file in `~/lightfall/plans/` defines one plan: a module-level variable
named `plan` that is a generator function yielding Bluesky messages. The
filename (without `.py`) becomes the plan name; files starting with `_` are
skipped.

```python
# ~/lightfall/plans/my_scan.py
import bluesky.plans as bp

def plan(detectors: list, motor, start: float = -10.0,
         stop: float = 10.0, num: int = 21):
    """Scan a motor while reading detectors."""
    yield from bp.scan(detectors, motor, start, stop, num)
```

`UserPlanService` (singleton, `lightfall.acquire.plans.UserPlanService`):

| Member | Description |
|--------|-------------|
| `get_instance()` | Singleton accessor. |
| `load_all_plans()` | Load every `*.py` in the plans directory; returns `(path, PlanInfo or Exception)` tuples. |
| `load_plan_from_file(path, commit_msg=None)` | Load one file; registers (or replaces) the plan. |
| `create_new_plan(name, description="", commit_msg=None)` | Create a file from the built-in template and load it. `name` must be a valid Python identifier. |
| `refresh_plans()` | Unload everything and reload from disk. |
| `get_plans_directory()` / `open_plans_folder()` | Path helpers. |
| Signals | `plan_loaded(PlanInfo)`, `plan_unloaded(name)`, `plan_error(path, message)`, `plans_refreshed()`. |

The service watches the directory with a `QFileSystemWatcher`: edits reload
the plan in place (`register_or_replace`), deletions unregister it. Every
change — including failed loads — is auto-committed to a local git
repository via `GitTracker`, so plan history is preserved.

## PlanRegistry

`lightfall.acquire.plans.registry.PlanRegistry` — the central catalog used
by the Plans panel and the agent. Singleton via
`PlanRegistry.get_instance()` (or the module-level `get_registry()`).

| Member | Description |
|--------|-------------|
| `register(name, func, category="general")` | Register a plan; raises `ValueError` on duplicate names. Returns the created `PlanInfo`. |
| `register_decorator(name=None, category="general")` | Decorator form; `name` defaults to the function name. |
| `register_or_replace(name, func, category="general")` | Replace-if-exists variant (used by hot-reload). |
| `unregister(name)` | Remove a plan; returns `True` if it existed. |
| `get_plan(name)` | `PlanInfo` or `None`. |
| `list_plans(category=None)` | All plans, optionally filtered by category. |
| `get_categories()` | Sorted category names. |
| `search(query)` | Case-insensitive match against name and description. |
| `plan_names` | Property: registered names. `name in registry` and `len(registry)` also work. |

## PlanInfo and ParameterInfo

`PlanInfo.from_function(name, func, category)` builds plan metadata by
introspection: it captures the signature, takes the first docstring
paragraph as the description, parses per-parameter descriptions from
Google- or NumPy-style docstrings, and extracts `>>>` examples.

`PlanInfo` fields: `name`, `func`, `signature`, `description`, `category`,
`parameters` (list of `ParameterInfo`), `examples`, `display_name`
(generated from `name` via `name_to_display_name()` when unset), and `icon`
(a `(color, letter)` tuple; defaults come from `PLAN_CATEGORY_ICONS` by
category). Helpers: `get_display_name()`, `get_icon()`,
`get_required_params()`, `get_optional_params()`.

`ParameterInfo` fields: `name`, `annotation`, `default`, `kind`,
`description`, plus derived `required` (no default) and `type_name`
(display-friendly type).

## Annotated UI vocabulary

The parameter editor reads `typing.Annotated` metadata from plan signatures
to render appropriate inputs. The vocabulary lives in
`lightfall.ui.annotations` (all are frozen dataclasses):

| Annotation | Effect |
|------------|--------|
| `Unit(suffix)` | Display a unit suffix next to a numeric input (e.g. `"eV"`, `"s"`, `"mm"`). |
| `Decimals(places)` | Decimal precision of a float spinbox. |
| `Range(min=None, max=None)` | Min/max bounds for a numeric input. |
| `Default(value)` | Default that overrides the signature default. |
| `DeviceFilter(device_class=None, category=None, group=None, source=None, name_pattern=None)` | Restrict a device-selector parameter; criteria AND together. |
| `DeviceFilterAny(*filters)` | OR-combination of `DeviceFilter`s (e.g. motors OR positioners). |
| `DeviceDefault(*names, pattern=None)` | Pre-select devices by name or regex. |
| `DeviceIcon(name)` | qtawesome icon for the device-selector button (`mdi6.` prepended when no prefix is given). |

```python
from typing import Annotated
from lightfall.ui.annotations import Unit, Range, DeviceFilter

def plan(
    detectors: Annotated[list, DeviceFilter(category="detector")],
    motor: Annotated[object, DeviceFilter(category="motor")],
    start: Annotated[float, Unit("mm")] = -10.0,
    stop: Annotated[float, Unit("mm")] = 10.0,
    num: Annotated[int, Range(min=1)] = 21,
):
    ...
```

This is the same vocabulary the built-in typed wrapper plans use, so user
plans annotated this way get identical form generation.

> 🖼️ **Image placeholder** — *Screenshot: the plan configuration form generated from an annotated signature — numeric spinboxes with unit suffixes and a filtered device-selector button.*

## Embedded plan UIs (`plan_with_ui`)

Plans can ship a runtime UI widget shown as a tab in the Plans panel while
the plan runs (`lightfall.acquire.plan_ui`):

- `PlanUI` — `QWidget` base for the widget; build the UI in `__init__`
  using `self._layout`.
- `PlanState` — `QObject` base for shared state between the running plan
  and its UI. Defines `status_changed = Signal(str)` and the
  `stop_requested` / `pause_requested` flags; subclasses add their own
  signals and attributes. The plan module keeps a module-level instance
  that both the plan function and the widget reference directly.
- `@plan_with_ui(UIClass)` — decorator that attaches a `PlanUI` subclass to
  a plan function (stored as the `_plan_ui_class` attribute). The Plans
  panel instantiates the UI when the plan is submitted.
- `get_plan_ui_class(plan_func)` — returns the attached class or `None`.

```python
from lightfall.acquire.plan_ui import PlanUI, plan_with_ui

class MyPlanUI(PlanUI):
    ...

@plan_with_ui(MyPlanUI)
def plan(detectors):
    yield from bps.count(detectors)
```

Qt signals are thread-safe across threads; plans should reset their state
object explicitly at the start of each run.

## Class reference

```{eval-rst}
.. autoclass:: lightfall.acquire.plans.registry.PlanRegistry
   :members:
   :show-inheritance:
```

```{eval-rst}
.. autoclass:: lightfall.acquire.plans.registry.PlanInfo
   :members:
   :show-inheritance:
```

```{eval-rst}
.. autoclass:: lightfall.acquire.plans.registry.ParameterInfo
   :members:
   :show-inheritance:
```

```{eval-rst}
.. autoclass:: lightfall.acquire.plans.user_plans.UserPlanService
   :members:
   :show-inheritance:
```

```{eval-rst}
.. automodule:: lightfall.ui.annotations
   :members:
```

```{eval-rst}
.. automodule:: lightfall.acquire.plan_ui
   :members:
```
