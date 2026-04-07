# Device Selector Dialog v2

**Date:** 2026-04-06
**Status:** Draft
**Scope:** Replace flat QListWidget device selector with tree-aware QTreeView dialog backed by proper Qt models

## Motivation

The current `DeviceSelectorDialog` uses a flat `QListWidget` showing only top-level devices. Bluesky plans like `count(detectors)` need to accept any readable ophyd object, including sub-components (e.g., `motor1.user_readback`). The dialog also has stale category icon mappings and no support for filtering by writability or ophyd kind.

## Design

### Data Model: `DeviceSelectionModel`

A `QAbstractItemModel` that builds a selectable tree from the `DeviceCatalog`.

**Tree structure:**
- Top-level items: devices from catalog (same as today)
- Children: ophyd components/signals, populated recursively from `component_names` / `_signals` / `_sig_attrs` (reusing the same traversal logic as `DeviceTreeModel._add_components`)

**Per-node data stored in internal items:**

| Field | Type | Source |
|---|---|---|
| `name` | `str` | Component name |
| `dotted_path` | `str` | Full dotted path from root (e.g., `"motor1.user_readback"`) |
| `category` | `DeviceCategory` | From `DeviceInfo` for top-level; inherited from parent for children |
| `is_writable` | `bool` | True if the ophyd object is put-capable (see Writable Detection below) |
| `kind` | `str \| None` | ophyd Kind name (`"hinted"`, `"normal"`, `"config"`, `"omitted"`) |
| `device_info` | `DeviceInfo \| None` | Only set on top-level device nodes |
| `ophyd_obj` | `Any \| None` | The ophyd object if instantiated |
| `node_type` | `NodeType` | `DEVICE` or `SIGNAL` (reuse existing enum) |

**Columns:** Name (column 0 only). The model is single-column; all metadata is used for filtering/tooltips, not display columns.

**Checkable items:** Each item has `Qt.ItemIsUserCheckable` flag. Check state is stored in the model (not selection model) so that checking a parent doesn't auto-check children — they're independently selectable. The checked items form the return value.

**Writable detection:** A signal is writable if it is not an instance of a read-only class (e.g., `EpicsSignalRO`, `SignalRO`). Checked via: `hasattr(obj, 'put') and not isinstance(obj, read-only base classes)`, or more robustly by checking if the class name ends in `RO` or if `obj._metadata.get('write_access', True)` is False. A device node is writable if it is a `PositionerBase` subclass or has at least one writable child signal.

**Population:** Tree is built once at dialog open from `DeviceCatalog.get_all_devices()`. Component children are added immediately (not lazily) since this is a short-lived dialog, not the persistent device panel. For devices not yet connected (no ophyd object), only the top-level node is shown (no children to enumerate).

**`show_tree` option:** When `False`, children are not added — the model contains only top-level device nodes (flat list behavior, backwards compatible with current dialog).

### Filter Proxy: `DeviceSelectionFilterProxy`

A `QSortFilterProxyModel` subclass with these filter dimensions, all AND-combined:

| Filter | Type | Default | Behavior |
|---|---|---|---|
| `categories` | `set[DeviceCategory] \| None` | `None` (all) | Match device category. Children inherit parent's category. |
| `writable_only` | `bool` | `False` | Show only items where `is_writable` is True |
| `kinds` | `set[str] \| None` | `None` (all) | Match ophyd Kind name |
| `filter_func` | `Callable[[dict], bool] \| None` | `None` | Custom filter; receives dict with `name`, `dotted_path`, `category`, `is_writable`, `kind`, `device_info` |
| `search_text` | `str` | `""` | Case-insensitive substring match on name, dotted path, or description |

**Recursive accept:** A parent node is shown if it passes the filter OR if any descendant passes the filter. This is the standard Qt pattern using `filterAcceptsRow` with recursive child checking.

**Sorting:** Override `lessThan` to delegate to an optional `sort_key: Callable[[dict], Any]` function. Default sort is alphabetical by name. The sort key receives the same dict as `filter_func`.

### Dialog: `DeviceSelectorDialog`

**Constructor parameters:**

```python
def __init__(
    self,
    catalog: DeviceCatalog,
    *,
    multi_select: bool = True,
    show_tree: bool = False,
    categories: set[DeviceCategory] | None = None,
    writable_only: bool = False,
    kinds: set[str] | None = None,
    filter_func: Callable[[dict], bool] | None = None,
    sort_key: Callable[[dict], Any] | None = None,
    initial_selection: list[str] | None = None,
    parent: QWidget | None = None,
) -> None:
```

**UI layout:**
- Search bar (QLineEdit with clear button) at top
- QTreeView with the model/proxy, single column
- Checkboxes on each item (multi_select=True) or radio-button-style exclusive check (multi_select=False)
- Status label showing count of checked items
- OK / Cancel button box

**Return value:** `get_selected_paths() -> list[str]` returns dotted paths of all checked items. For top-level devices, this is just the device name (e.g., `"det1"`). For children, it's the dotted path (e.g., `"motor1.user_readback"`).

**Initial selection:** `initial_selection` is a list of dotted paths. On dialog open, items matching these paths are pre-checked. This replaces the existing `DeviceDefault` annotation behavior within the dialog (the annotation still exists for plan authors).

### DeviceParameterItem Updates

**Icon on the button:** Replace the "..." text on the `QPushButton` with a QtAwesome icon.

Icon resolution chain:
1. Explicit `icon` option on the parameter spec (e.g., `"mdi6.engine"`)
2. Auto-derived from `categories` filter if exactly one category is set:
   - `motor` -> `mdi6.engine`
   - `detector` -> `mdi6.camera`
   - `controller` -> `mdi6.tune-variant`
3. Default: `mdi6.microwave`

If the icon string has no dot (no prefix), prepend `mdi6.` automatically.

**New annotation: `DeviceIcon`**

```python
@dataclass(frozen=True)
class DeviceIcon:
    """QtAwesome icon identifier for the device parameter button."""
    name: str
```

Usage in plan signatures:
```python
def scan(
    motor: Annotated[Device, DeviceFilter(category="motor"), DeviceIcon("mdi6.engine")],
    detectors: Annotated[list[Readable], DeviceFilter(category="detector")],  # auto: mdi6.camera
):
    ...
```

**New options passed through to dialog:** The parameter spec gains these keys that are forwarded to the dialog constructor: `show_tree`, `categories`, `writable_only`, `kinds`, `filter_func`, `sort_key`. The existing `device_filter`/`category_filter`/`device_default` options are replaced by the new parameters.

### Annotation Updates

**`DeviceFilter` changes:** The `category` field changes from `str | None` to `str | set[str] | None` to support multi-category filtering. When a `DeviceFilter` is present in annotations, `PlanConfigWidget._build_param_spec` extracts the category into the `categories` parameter (converting to `set[DeviceCategory]`).

**New annotation `DeviceIcon`** added to `annotations.py` and exported.

**`DeviceFilterAny` deprecation:** With multi-category support in `DeviceFilter`, the primary use case for `DeviceFilterAny` (OR-ing category filters) is handled directly. `DeviceFilterAny` remains functional but is no longer needed for category unions.

### Backwards Compatibility

- The old `category_filter: str` parameter on `DeviceSelectorDialog.__init__` is removed. All callers use the new `categories` parameter.
- `DeviceDefault` annotation still works — `PlanConfigWidget` translates it to `initial_selection`.
- `DeviceFilter` annotation still works — `PlanConfigWidget` translates its fields to the new dialog parameters.
- The `DEVICE_CATEGORY_ICONS` dict and `create_device_icon` function in `device_selector.py` are removed (no longer used for list item icons; the tree view doesn't need per-row category icons since category is a filter, not a display column).

### File Changes

| File | Change |
|---|---|
| `ui/widgets/device_selector.py` | Replace `DeviceSelectorDialog`, `DeviceParameterItem`; add `DeviceSelectionModel`, `DeviceSelectionFilterProxy` |
| `ui/annotations.py` | Add `DeviceIcon`; update `DeviceFilter.category` type to `str \| set[str] \| None` |
| `ui/widgets/plan_config.py` | Update `_build_param_spec` to translate annotations to new dialog parameters |
| `ui/models/device_tree.py` | No changes (the new model is separate; shared logic can be extracted later if needed) |

### What This Does NOT Change

- The Device Panel's `DeviceTreeModel` — that's the persistent tree in the main UI, not the selector dialog
- Plan execution / device resolution in `BlueskyPanel`
- The `DeviceCatalog` API
- How plans are registered or discovered

## Testing

- Unit tests for `DeviceSelectionModel`: tree structure, dotted paths, writable detection
- Unit tests for `DeviceSelectionFilterProxy`: each filter dimension independently and combined
- Unit tests for `DeviceParameterItem`: icon resolution chain
- Integration test: open dialog with mock catalog, check items, verify returned paths
