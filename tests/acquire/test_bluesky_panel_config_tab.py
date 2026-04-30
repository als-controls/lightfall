"""Tests for BlueskyPanel's Config-tab lifecycle."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QTabWidget

from lucid.acquire.plan_ui import PlanUI, plan_with_ui
from lucid.acquire.plans import PlanInfo
from lucid.ui.panels.bluesky_panel import BlueskyPanel
from lucid.ui.widgets.plan_config import PlanConfigWidget


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

    def test_second_plan_reuses_tab_and_retitles(self, qtbot):
        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        panel._on_plan_selected(_plan("alpha"))
        assert tab_widget.count() == 2
        assert tab_widget.tabText(1).lower().endswith("alpha")

        panel._on_plan_selected(_plan("beta"))

        assert tab_widget.count() == 2  # reused, not added
        assert tab_widget.tabText(1).lower().endswith("beta")
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
