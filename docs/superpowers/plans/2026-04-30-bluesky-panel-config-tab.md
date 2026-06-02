# Bluesky Panel Config Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `PlanConfigWidget` out of the vertical splitter inside the BlueskyPanel "Plans" tab and into its own dynamically-managed "Config: <plan>" tab in the existing `QTabWidget`.

**Architecture:** Add a single helper `_show_plan_config(plan_info)` on `BlueskyPanel` that lazily inserts `PlanConfigWidget` as a tab on first plan selection and reuses/retitles it thereafter. Route both the user double-click path (`_on_plan_selected`) and the MCP path (`select_plan`) through that helper. Replace cached integer tab indices with `QTabWidget.indexOf(widget)` lookups so the state stays correct as tabs are inserted/removed.

**Tech Stack:** PySide6 (`QTabWidget`, `QWidget`, `QToolBar`), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-04-30-bluesky-panel-config-tab-design.md`

---

## File Structure

- **Modify:** `src/lightfall/ui/panels/bluesky_panel.py`
  - Drop the `QSplitter(Vertical)` from `_setup_ui`; put `PlanSelectorWidget` directly into the "Plans" tab.
  - Add `_show_plan_config(plan_info)` helper.
  - Route `_on_plan_selected` and `select_plan` through it.
  - Replace `_running_plan_tab_index` int with `indexOf(self._running_plan_ui)` lookups.
- **Modify:** `tests/acquire/test_plan_ui_integration.py`
  - Add new test class for Config-tab lifecycle (or sibling file `test_bluesky_panel_config_tab.py` — see Task 2).

No new files in `src/`. No public API changes other than UX behavior.

---

## Task 1: Restructure `_setup_ui` — remove splitter, eager-construct `PlanConfigWidget`

**Files:**
- Modify: `src/lightfall/ui/panels/bluesky_panel.py:111-149`
- Test: `tests/acquire/test_plan_ui_integration.py` (existing `test_initial_one_tab`, `test_panel_has_tab_widget`)

This is a refactor with behavior preserved at the "still one tab on init" level. After this task, no Config tab is created yet on selection — that's Task 3.

- [ ] **Step 1: Run the existing tests to confirm baseline**

```bash
cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/acquire/test_plan_ui_integration.py -v
```

Expected: 5 tests pass.

- [ ] **Step 2: Modify `_setup_ui` to drop the splitter**

In `src/lightfall/ui/panels/bluesky_panel.py`, replace the body of `_setup_ui` (currently lines 111–149) with:

```python
    def _setup_ui(self) -> None:
        """Set up the panel UI."""
        # Toolbar for plan actions
        self._toolbar = QToolBar()
        self._toolbar.setMovable(False)
        self._setup_toolbar()
        self._layout.addWidget(self._toolbar)

        # QTabWidget hosts: "Plans" (always), "Config: <plan>" (on demand),
        # "Running: <plan>" (on demand).
        self._tab_widget = QTabWidget()
        self._tab_widget.setTabBarAutoHide(True)

        # Tab 0: Plans — just the selector.
        self._plan_selector = PlanSelectorWidget()
        self._plan_selector.plan_selected.connect(self._on_plan_selected)
        self._tab_widget.addTab(self._plan_selector, "Plans")

        # PlanConfigWidget is constructed eagerly so set_catalog() etc. work
        # before the user has opened a plan. It is added to the tab widget
        # lazily on first plan selection (see _show_plan_config).
        self._plan_config = PlanConfigWidget()
        self._plan_config.run_requested.connect(self._on_run_requested)
        self._config_tab_added: bool = False

        self._layout.addWidget(self._tab_widget)

        # Running plan UI state
        self._running_plan_ui: PlanUI | None = None

        # Auto-configure with RunEngine and PlanRegistry singletons
        self._auto_configure()
```

Note: `QSplitter` and `QVBoxLayout` are no longer needed inside `_setup_ui`. Leave the module-level imports alone — `QSplitter` is also unused after this change but doesn't hurt; remove it from the import only if it has no other uses (it doesn't, in `bluesky_panel.py`). Adjust the import line:

```python
from PySide6.QtWidgets import (
    QDialog,
    QTabWidget,
    QToolBar,
    QWidget,
)
```

(Remove `QSplitter` and `QVBoxLayout`.)

Also delete the now-unused `_running_plan_tab_index` initialization. Replace any later use with `self._tab_widget.indexOf(self._running_plan_ui)`.

- [ ] **Step 3: Update `_maybe_create_plan_ui` and `_on_plan_ui_finished` to use `indexOf`**

Replace `src/lightfall/ui/panels/bluesky_panel.py:411-436`:

```python
    def _maybe_create_plan_ui(self, plan_info: PlanInfo) -> None:
        """If the plan has a _plan_ui_class, create a tab for it."""
        ui_class = get_plan_ui_class(plan_info.func)
        if ui_class is None:
            return

        # One plan UI at a time — remove any existing one
        if self._running_plan_ui is not None:
            self._on_plan_ui_finished()

        ui = ui_class()
        self._running_plan_ui = ui
        index = self._tab_widget.addTab(ui, f"Running: {plan_info.name}")
        self._tab_widget.setCurrentIndex(index)

    def _on_plan_ui_finished(self) -> None:
        """Remove the running plan UI tab, if any."""
        if self._running_plan_ui is None:
            return
        index = self._tab_widget.indexOf(self._running_plan_ui)
        if index >= 0:
            self._tab_widget.removeTab(index)
        self._running_plan_ui.deleteLater()
        self._running_plan_ui = None
```

- [ ] **Step 4: Run tests — confirm existing behavior preserved**

```bash
cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/acquire/test_plan_ui_integration.py -v
```

Expected: All 5 existing tests still pass. `test_initial_one_tab` still sees `count() == 1` (Plans only). `test_adds_tab_for_plan_with_ui` still sees `count() == 2` because that test calls `_on_run_requested` directly without going through `_on_plan_selected` — no Config tab is created.

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs && git add src/lightfall/ui/panels/bluesky_panel.py
git commit -m "refactor(bluesky-panel): hoist PlanConfigWidget out of Plans tab splitter

Plans tab now contains only PlanSelectorWidget. PlanConfigWidget is
constructed eagerly but not yet inserted into a tab; that wiring lands
in the next commit. Replace _running_plan_tab_index with indexOf lookups."
```

---

## Task 2: Add `_show_plan_config` helper with full lifecycle

**Files:**
- Modify: `src/lightfall/ui/panels/bluesky_panel.py` (add new method)
- Create: `tests/acquire/test_bluesky_panel_config_tab.py`

We put new tests in a sibling file rather than appending to `test_plan_ui_integration.py` so the Config-tab tests have a clear home. Existing tests stay where they are.

- [ ] **Step 1: Write the failing test for first-call behavior**

Create `tests/acquire/test_bluesky_panel_config_tab.py`:

```python
"""Tests for BlueskyPanel's Config-tab lifecycle."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QTabWidget

from lightfall.acquire.plan_ui import PlanUI, plan_with_ui
from lightfall.acquire.plans import PlanInfo
from lightfall.ui.panels.bluesky_panel import BlueskyPanel
from lightfall.ui.widgets.plan_config import PlanConfigWidget


def _plan(name: str) -> PlanInfo:
    """Build a minimal PlanInfo for `name`."""
    def fn():
        yield
    fn.__name__ = name
    return PlanInfo.from_function(name, fn, category="test")


class DummyPlanUI(PlanUI):
    pass


@plan_with_ui(DummyPlanUI)
def plan_with_ui_fn():
    yield


class TestConfigTabLifecycle:
    def test_first_selection_adds_config_tab(self, qtbot):
        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        plan_info = _plan("alpha")
        panel._on_plan_selected(plan_info)

        assert tab_widget.count() == 2
        assert tab_widget.tabText(1).startswith("Config: ")
        assert tab_widget.currentWidget() is panel._plan_config
        assert isinstance(tab_widget.currentWidget(), PlanConfigWidget)
        assert panel._plan_config.current_plan is not None
        assert panel._plan_config.current_plan.name == "alpha"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/acquire/test_bluesky_panel_config_tab.py::TestConfigTabLifecycle::test_first_selection_adds_config_tab -v
```

Expected: FAIL — `tab_widget.count()` is still 1 because `_on_plan_selected` currently only calls `set_plan`, not `addTab`.

- [ ] **Step 3: Add `_show_plan_config` and wire `_on_plan_selected`**

In `src/lightfall/ui/panels/bluesky_panel.py`, add the new helper just before `_on_plan_selected`:

```python
    def _show_plan_config(self, plan_info: PlanInfo) -> None:
        """Show the Config tab for `plan_info`.

        On first call, adds ``self._plan_config`` as a new tab. On
        subsequent calls with a different plan, updates the widget's
        plan and retitles the tab. Always brings the tab to the front.
        """
        title = f"Config: {plan_info.get_display_name()}"

        if not self._config_tab_added:
            self._tab_widget.addTab(self._plan_config, title)
            self._config_tab_added = True
            self._plan_config.set_plan(plan_info)
        else:
            current = self._plan_config.current_plan
            if current is None or current.name != plan_info.name:
                self._plan_config.set_plan(plan_info)
                index = self._tab_widget.indexOf(self._plan_config)
                if index >= 0:
                    self._tab_widget.setTabText(index, title)

        self._tab_widget.setCurrentWidget(self._plan_config)
```

Replace the body of `_on_plan_selected` (currently `src/lightfall/ui/panels/bluesky_panel.py:373-382`):

```python
    @Slot(object)
    def _on_plan_selected(self, plan_info: PlanInfo) -> None:
        """Handle plan selection from selector.

        Args:
            plan_info: Selected plan.
        """
        self._show_plan_config(plan_info)
        self._current_plan_name = plan_info.name
        logger.debug(f"Plan selected: {plan_info.name}")
```

- [ ] **Step 4: Run the test — should pass**

```bash
cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/acquire/test_bluesky_panel_config_tab.py::TestConfigTabLifecycle::test_first_selection_adds_config_tab -v
```

Expected: PASS.

- [ ] **Step 5: Add the remaining lifecycle tests**

Append to `tests/acquire/test_bluesky_panel_config_tab.py`:

```python
    def test_second_plan_reuses_tab_and_retitles(self, qtbot):
        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        panel._on_plan_selected(_plan("alpha"))
        assert tab_widget.count() == 2
        assert tab_widget.tabText(1).endswith("alpha")

        panel._on_plan_selected(_plan("beta"))

        assert tab_widget.count() == 2  # reused, not added
        assert tab_widget.tabText(1).endswith("beta")
        assert panel._plan_config.current_plan.name == "beta"
        assert tab_widget.currentWidget() is panel._plan_config

    def test_same_plan_double_click_preserves_state(self, qtbot):
        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        plan_info = _plan("alpha")
        panel._on_plan_selected(plan_info)

        # Spy on set_plan to confirm it isn't re-invoked for the same plan.
        panel._plan_config.set_plan = MagicMock(wraps=panel._plan_config.set_plan)
        panel._on_plan_selected(plan_info)

        assert tab_widget.count() == 2
        assert tab_widget.currentWidget() is panel._plan_config
        panel._plan_config.set_plan.assert_not_called()

    def test_config_tab_persists_across_run(self, qtbot):
        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        # Open Config tab
        ui_plan = PlanInfo.from_function(
            "plan_with_ui_fn", plan_with_ui_fn, category="test"
        )
        panel._on_plan_selected(ui_plan)
        assert tab_widget.count() == 2

        # Mock engine and start a run — should add Running tab on top
        panel._engine = MagicMock()
        panel._on_run_requested(ui_plan, {})
        assert tab_widget.count() == 3
        assert tab_widget.tabText(2).startswith("Running: ")

        # Plan finishes — Running tab goes, Config stays
        panel._on_plan_ui_finished()
        assert tab_widget.count() == 2
        assert tab_widget.tabText(1).startswith("Config: ")
```

- [ ] **Step 6: Run the new tests — all should pass**

```bash
cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/acquire/test_bluesky_panel_config_tab.py -v
```

Expected: 4 PASS.

- [ ] **Step 7: Run the existing integration tests — still pass**

```bash
cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/acquire/test_plan_ui_integration.py -v
```

Expected: 5 PASS. `test_adds_tab_for_plan_with_ui` still sees `count() == 2` because it calls `_on_run_requested` directly without `_on_plan_selected`.

- [ ] **Step 8: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs && git add src/lightfall/ui/panels/bluesky_panel.py tests/acquire/test_bluesky_panel_config_tab.py
git commit -m "feat(bluesky-panel): open plans in dedicated Config tab

Double-clicking a plan now opens its parameter UI in a 'Config: <plan>'
tab instead of below the list in a vertical splitter. The single tab
is reused across plan switches; switching plans retitles, and
re-selecting the same plan only switches focus."
```

---

## Task 3: Route MCP `select_plan` through `_show_plan_config`

**Files:**
- Modify: `src/lightfall/ui/panels/bluesky_panel.py:352-369` (the `select_plan` method)
- Test: `tests/acquire/test_bluesky_panel_config_tab.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/acquire/test_bluesky_panel_config_tab.py`:

```python
    def test_mcp_select_plan_opens_config_tab(self, qtbot):
        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        # Stub a registry that returns our PlanInfo for "alpha"
        panel._registry = MagicMock()
        panel._registry.get_plan.return_value = _plan("alpha")

        ok = panel.select_plan("alpha")

        assert ok is True
        assert tab_widget.count() == 2
        assert tab_widget.currentWidget() is panel._plan_config
        assert panel._plan_config.current_plan.name == "alpha"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/acquire/test_bluesky_panel_config_tab.py::TestConfigTabLifecycle::test_mcp_select_plan_opens_config_tab -v
```

Expected: FAIL — `tab_widget.count()` is 1 because `select_plan` calls `set_plan` directly without going through `_show_plan_config`.

- [ ] **Step 3: Update `select_plan` to use `_show_plan_config`**

Replace the body of `select_plan` in `src/lightfall/ui/panels/bluesky_panel.py`:

```python
    def select_plan(self, plan_name: str) -> bool:
        """Select a plan by name (for MCP tools).

        Args:
            plan_name: Name of the plan to select.

        Returns:
            True if plan was found and selected.
        """
        if self._registry is None:
            return False

        plan_info = self._registry.get_plan(plan_name)
        if plan_info:
            self._show_plan_config(plan_info)
            self._current_plan_name = plan_name
            return True
        return False
```

- [ ] **Step 4: Run the new test — should pass**

```bash
cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/acquire/test_bluesky_panel_config_tab.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Run the full BlueskyPanel-touching test set**

```bash
cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/acquire/test_plan_ui_integration.py tests/acquire/test_bluesky_panel_config_tab.py -v
```

Expected: 10 PASS (5 in `test_plan_ui_integration.py`, 5 in `test_bluesky_panel_config_tab.py`).

- [ ] **Step 6: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs && git add src/lightfall/ui/panels/bluesky_panel.py tests/acquire/test_bluesky_panel_config_tab.py
git commit -m "feat(bluesky-panel): MCP select_plan opens Config tab

Programmatic plan selection via the MCP introspection API now produces
the same UX as a user double-click: opens (or reuses) the Config tab
and switches focus."
```

---

## Task 4: Manual smoke test in the running app

**Files:** none (verification only)

The unit tests cover the state transitions but don't exercise the actual Qt rendering. Per `CLAUDE.md` and project memory, UI changes need a real run-through.

- [ ] **Step 1: Launch Lightfall**

```bash
cd ~/PycharmProjects/ncs/ncs && .venv/Scripts/python -m lightfall
```

- [ ] **Step 2: Verify initial state**

- BlueskyPanel is visible in the left sidebar.
- The tab bar is hidden (only "Plans" exists; `tabBarAutoHide` keeps it hidden when there's a single tab).
- The plan list and the right-side details panel are visible. There is no parameter form below the list anymore.

- [ ] **Step 3: Verify single-click**

Click a plan once. The right-side details update. No new tab appears, the active tab stays "Plans".

- [ ] **Step 4: Verify double-click → Config tab**

Double-click plan A.

- The tab bar appears with "Plans" and "Config: <A's display name>".
- The active tab is the Config tab and shows `PlanConfigWidget` for A.
- The "Plans" tab is still clickable and contains the selector.

- [ ] **Step 5: Verify second double-click on a different plan**

Double-click plan B.

- The tab bar still has exactly two tabs.
- The second tab is now titled "Config: <B's display name>".
- The widget shows B's parameters.

- [ ] **Step 6: Verify same-plan double-click preserves typed parameters**

Switch back to "Plans", type a value into one of B's parameters first, then double-click B again.

- The tab is the same instance, the typed value is still there.

- [ ] **Step 7: Verify Config persists across run**

With B selected and a `_plan_ui_class` plan available (e.g. an adaptive plan), click Run.

- A "Running: B" tab appears and becomes active.
- The "Config: B" tab is still there.

- [ ] **Step 8: Verify Running cleanup**

When the plan finishes (or use the toolbar's RunEngine controls to abort).

- The "Running: B" tab disappears.
- "Config: B" remains. Active tab is "Config: B" (or whatever was active when Running was removed — Qt's default).

- [ ] **Step 9: Commit smoke-test results in the conversation**

If anything in steps 2–8 doesn't behave as described, stop and reproduce in a unit test before fixing. If everything looks right, no code change is needed — this task is verification only.

---

## Verification summary

After all tasks:

- `tests/acquire/test_plan_ui_integration.py` — 5 tests pass (unchanged).
- `tests/acquire/test_bluesky_panel_config_tab.py` — 5 tests pass (new).
- Manual smoke test in the running app matches the Behavior matrix in the spec.
- `git log --oneline` shows three feature commits on top of `9293f55` (the spec commit).
