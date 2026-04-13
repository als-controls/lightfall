"""Integration tests for plan UI lifecycle in the BlueskyPanel."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QTabWidget

from lucid.acquire.plan_ui import PlanUI, plan_with_ui


class DummyPlanUI(PlanUI):
    """Minimal UI for testing."""
    pass


@plan_with_ui(DummyPlanUI)
def dummy_plan_with_ui():
    """A dummy plan that has a UI attached."""
    yield


def dummy_plan_no_ui():
    """A dummy plan with no UI."""
    yield


class TestBlueskyPanelTabbing:
    def test_panel_has_tab_widget(self, qtbot):
        from lucid.ui.panels.bluesky_panel import BlueskyPanel

        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)
        assert tab_widget is not None
        assert tab_widget.tabBarAutoHide() is True

    def test_initial_one_tab(self, qtbot):
        from lucid.ui.panels.bluesky_panel import BlueskyPanel

        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)
        assert tab_widget.count() == 1

    def test_adds_tab_for_plan_with_ui(self, qtbot):
        from lucid.acquire.plans import PlanInfo
        from lucid.ui.panels.bluesky_panel import BlueskyPanel

        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        plan_info = PlanInfo.from_function(
            "dummy_plan_with_ui", dummy_plan_with_ui, category="test"
        )

        panel._engine = MagicMock()
        panel._engine.__call__ = MagicMock()

        panel._on_run_requested(plan_info, {})

        assert tab_widget.count() == 2
        ui_widget = tab_widget.widget(1)
        assert isinstance(ui_widget, DummyPlanUI)

    def test_no_tab_for_plan_without_ui(self, qtbot):
        from lucid.acquire.plans import PlanInfo
        from lucid.ui.panels.bluesky_panel import BlueskyPanel

        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        plan_info = PlanInfo.from_function(
            "dummy_plan_no_ui", dummy_plan_no_ui, category="test"
        )

        panel._engine = MagicMock()
        panel._on_run_requested(plan_info, {})

        assert tab_widget.count() == 1

    def test_removes_tab_on_finish(self, qtbot):
        from lucid.acquire.plans import PlanInfo
        from lucid.ui.panels.bluesky_panel import BlueskyPanel

        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        plan_info = PlanInfo.from_function(
            "dummy_plan_with_ui", dummy_plan_with_ui, category="test"
        )
        panel._engine = MagicMock()
        panel._on_run_requested(plan_info, {})
        assert tab_widget.count() == 2

        panel._on_plan_ui_finished()
        assert tab_widget.count() == 1
