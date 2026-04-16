"""Tests for the plan UI framework."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from lucid.acquire.plan_ui import PlanState, PlanUI, plan_with_ui


class TestPlanWithUIDecorator:
    def test_attaches_ui_class(self):
        class MyUI(PlanUI):
            pass

        @plan_with_ui(MyUI)
        def my_plan():
            yield

        assert my_plan._plan_ui_class is MyUI

    def test_preserves_function(self):
        class MyUI(PlanUI):
            pass

        @plan_with_ui(MyUI)
        def my_plan(arg):
            yield arg

        gen = my_plan(42)
        assert next(gen) == 42

    def test_get_plan_ui_class_helper(self):
        from lucid.acquire.plan_ui import get_plan_ui_class

        class MyUI(PlanUI):
            pass

        @plan_with_ui(MyUI)
        def my_plan():
            yield

        def plain_plan():
            yield

        assert get_plan_ui_class(my_plan) is MyUI
        assert get_plan_ui_class(plain_plan) is None


class TestPlanState:
    def test_default_flags(self, qtbot):
        state = PlanState()
        assert state.stop_requested is False
        assert state.pause_requested is False

    def test_flags_writable(self, qtbot):
        state = PlanState()
        state.stop_requested = True
        state.pause_requested = True
        assert state.stop_requested is True
        assert state.pause_requested is True

    def test_status_signal(self, qtbot):
        state = PlanState()
        received = []
        state.status_changed.connect(received.append)
        state.status_changed.emit("running")
        assert received == ["running"]


class TestPlanUI:
    def test_is_qwidget(self, qtbot):
        ui = PlanUI()
        qtbot.addWidget(ui)
        assert isinstance(ui, QWidget)
