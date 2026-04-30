# Bluesky Panel Config Tab — Design Spec

**Date:** 2026-04-30
**Status:** Draft

## Summary

Restructure the BlueskyPanel "Plans" tab so that selecting a plan opens its parameter UI in a dedicated tab rather than below the plan list in a vertical splitter. The existing `QTabWidget` remains the panel's main container; a new "Config: <plan>" tab joins the existing "Plans" and dynamic "Running: <plan>" tabs.

## Goals

- Give the plan parameter UI the full vertical space of the panel when active.
- Reuse the existing tab machinery instead of introducing a parallel `QStackedWidget`.
- Preserve the existing "Running: <plan>" tab behavior unchanged.
- Allow the user to compare the configured parameters side-by-side with the running plan UI.

## Non-Goals

- Changes to the right-side plan-details panel inside `PlanSelectorWidget`.
- Changes to the toolbar (New Plan / Refresh / Open Folder).
- Changes to engine signal wiring or plan-execution flow.
- Multiple Config tabs open simultaneously (one at a time, like Running).
- Closable (`X`) tabs — none of the three tabs get a close button.

---

## 1. Panel Layout

`BlueskyPanel`'s main widget remains the existing `QTabWidget` (with `tabBarAutoHide=True`). The vertical `QSplitter` previously inside the "Plans" tab is removed; that tab now contains only the `PlanSelectorWidget`.

- **Tab "Plans"**: Permanent, non-closable. Contains `PlanSelectorWidget` (with its own internal horizontal list/details splitter, untouched).
- **Tab "Config: <plan>"**: Dynamic, non-closable. Created on first plan selection and reused thereafter. Contains the single `PlanConfigWidget` instance.
- **Tab "Running: <plan>"**: Dynamic, non-closable. Existing behavior — created in `_on_run_requested` when the plan has a `_plan_ui_class`, removed in `_on_plan_ui_finished`.

When only "Plans" exists, `tabBarAutoHide` keeps the tab bar hidden. As soon as a Config or Running tab is added, the bar appears.

## 2. Tab Lifecycle

| Tab | Created | Updated | Removed |
|---|---|---|---|
| Plans | `_setup_ui` | never | never |
| Config: <plan> | First call to `_show_plan_config(plan_info)` (driven by `_on_plan_selected` or MCP `select_plan`) | Subsequent call with a different plan: same widget instance, `set_plan(plan_info)`, retitle tab to `"Config: <new>"`, switch focus. Same plan: just `setCurrentWidget`. | never (lives until panel destroyed) |
| Running: <plan> | `_on_run_requested`, only if plan has `_plan_ui_class` | Replaced if a new run starts (existing behavior) | `_on_plan_ui_finished` (existing) |

`PlanConfigWidget` is constructed eagerly in `_setup_ui` (so `set_catalog`, `set_engine`, etc., still work before its tab exists). It is added to the `QTabWidget` lazily on first plan selection.

## 3. State

```python
self._plan_config: PlanConfigWidget         # constructed eagerly in _setup_ui
self._config_tab_added: bool = False        # whether _plan_config has been inserted into the tab widget
self._running_plan_ui: PlanUI | None        # unchanged
```

Tab indices are computed via `self._tab_widget.indexOf(widget)` on demand rather than cached, so insertions/removals don't desync state. The existing `_running_plan_tab_index: int` is replaced with `indexOf(self._running_plan_ui)` for consistency.

## 4. Method Changes in `BlueskyPanel`

### New helper

```python
def _show_plan_config(self, plan_info: PlanInfo) -> None:
    """Add the Config tab on first call; on subsequent calls update the
    tab's plan and title. Always switches focus to the Config tab."""
```

Behavior:
1. If `self._config_tab_added` is False: add `self._plan_config` as a new tab titled `f"Config: {plan_info.get_display_name()}"`, set `self._config_tab_added = True`, call `self._plan_config.set_plan(plan_info)`.
2. Else if `self._plan_config.current_plan` is None or its `.name` differs from `plan_info.name`: call `self._plan_config.set_plan(plan_info)` and retitle the tab via `setTabText(self._tab_widget.indexOf(self._plan_config), f"Config: {plan_info.get_display_name()}")`.
3. Else (same plan): no-op on the widget; only switch focus.
4. Always: `self._tab_widget.setCurrentWidget(self._plan_config)`.

### `_on_plan_selected(plan_info)`

Replace the inline `self._plan_config.set_plan(plan_info)` with a call to `_show_plan_config(plan_info)`. `self._current_plan_name` assignment stays.

### `select_plan(plan_name)` (MCP)

After the existing registry lookup succeeds, call `_show_plan_config(plan_info)` instead of (or in addition to) the direct `self._plan_config.set_plan(plan_info)`. This ensures programmatic selection produces the same UX as double-click.

### `_on_run_requested`, `_maybe_create_plan_ui`, `_on_plan_ui_finished`

No semantic changes. Internally, replace `self._running_plan_tab_index` with `indexOf(self._running_plan_ui)` lookups.

### `_setup_ui`

- Remove the vertical `QSplitter` from `selector_container`.
- The "Plans" tab now wraps `PlanSelectorWidget` directly (still inside a `selector_container` `QWidget` so future toolbars at the top of that tab remain easy to add — or, simpler, add `PlanSelectorWidget` as the tab's widget directly).
- `PlanConfigWidget` is constructed but not added to any tab yet.

## 5. Behavior matrix

| User action | Tabs after action | Active tab |
|---|---|---|
| Open panel | Plans | Plans |
| Single-click plan in list | Plans | Plans (right-side details updates; no tab change) |
| Double-click plan A | Plans, Config: A | Config: A |
| Double-click plan A again | Plans, Config: A | Config: A (params preserved) |
| Double-click plan B | Plans, Config: B | Config: B (same widget, retitled) |
| Click Run on plan B (with `_plan_ui_class`) | Plans, Config: B, Running: B | Running: B |
| Click Run on plan B (without `_plan_ui_class`) | Plans, Config: B | Config: B |
| Plan B finishes / aborts | Plans, Config: B | Config: B |
| Switch back to Plans tab | Plans, Config: B | Plans |

## 6. Test Plan

`tests/acquire/test_plan_ui_integration.py` already covers Running-tab lifecycle. Existing assertions:

- `test_initial_one_tab` — `count() == 1`. Still true.
- `test_adds_tab_for_plan_with_ui` — calls `_on_run_requested` directly without prior selection. Still produces `count() == 2` (Plans + Running). Still passes.
- `test_no_tab_for_plan_without_ui` — `count() == 1` after run with no UI. Still passes.
- `test_removes_tab_on_finish` — after `_on_plan_ui_finished`, `count() == 1`. Still passes.

New tests (added to the same file or a sibling):

1. **Double-click adds Config tab** — call `_on_plan_selected(plan_info)` → `count() == 2`, current tab is the Config tab, title is `"Config: <display name>"`.
2. **Second plan reuses Config tab** — select A then B → still `count() == 2`, tab title now reflects B, `_plan_config` holds B.
3. **Same plan double-click** — select A twice → `count() == 2`, `set_plan` called once on the widget (via mock or by checking that intermediate state isn't reset).
4. **Config persists across run** — select A, then `_on_run_requested(A_with_ui, {})` → `count() == 3`. After `_on_plan_ui_finished` → `count() == 2` (Config tab survives).
5. **MCP `select_plan` adds Config tab** — `panel.select_plan("name")` → `count() == 2`, current tab is Config.

## 7. Out of scope

- Plan details widget on the right side of `PlanSelectorWidget`.
- Toolbar actions.
- Engine signal wiring.
- Closable tabs.
- Multiple simultaneous Config tabs.
