"""Plans show where they come from: details pane and tooltip."""

from __future__ import annotations

import pytest

from lightfall.acquire.plans.registry import PlanInfo
from lightfall.ui.widgets.plan_selector import PlanListModel, PlanSelectorWidget


def _plan(source: str) -> PlanInfo:
    def my_plan(x: int = 1):
        """Do a thing."""
        yield from ()

    info = PlanInfo.from_function("my_plan", my_plan)
    info.source = source
    return info


class _FakeRegistry:
    def __init__(self, plans):
        self._plans = plans

    def list_plans(self):
        return self._plans

    def get_categories(self):
        return []


@pytest.mark.parametrize(
    "source,label",
    [("local", "Mine"), ("branch:7.0.1.1", "Branch 7.0.1.1"), ("core", "Built-in")],
)
def test_plan_details_show_source_label(qtbot, source, label):
    widget = PlanSelectorWidget()
    qtbot.addWidget(widget)
    html = widget._format_plan_details(_plan(source))
    assert f"<b>From:</b> {label}" in html


def test_plan_details_escape_unknown_source(qtbot):
    widget = PlanSelectorWidget()
    qtbot.addWidget(widget)
    html = widget._format_plan_details(_plan("<i>z</i>"))
    assert "<i>z</i>" not in html
    assert "&lt;i&gt;z&lt;/i&gt;" in html


def test_plan_tooltip_has_source(qtbot):
    model = PlanListModel(_FakeRegistry([_plan("branch:7.0.1.1")]))
    tip = model.item(0).toolTip()
    assert "Branch 7.0.1.1" in tip
    assert "my_plan" in tip
