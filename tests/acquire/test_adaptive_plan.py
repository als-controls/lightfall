"""Tests for the adaptive experiment plan (module-level state + plan logic)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QPushButton

from lucid.acquire.plans.adaptive import (
    AdaptiveExperimentPanel,
    AdaptivePlanState,
    _state,
    adaptive_experiment,
)


class TestAdaptivePlanState:
    def test_subclasses_plan_state(self, qtbot):
        from lucid.acquire.plan_ui import PlanState
        assert issubclass(AdaptivePlanState, PlanState)

    def test_iteration_signal(self, qtbot):
        state = AdaptivePlanState()
        received = []
        state.iteration_changed.connect(received.append)
        state.iteration_changed.emit(7)
        assert received == [7]

    def test_targets_received_signal(self, qtbot):
        state = AdaptivePlanState()
        received = []
        state.targets_received.connect(received.append)
        state.targets_received.emit(3)
        assert received == [3]


class TestAdaptiveExperimentPanel:
    def test_creates_widget(self, qtbot):
        panel = AdaptiveExperimentPanel()
        qtbot.addWidget(panel)
        # Has at least stop and pause buttons
        buttons = panel.findChildren(QPushButton)
        labels = [b.text() for b in buttons]
        assert "Stop" in labels
        assert "Pause" in labels or "Pause/Resume" in labels

    def test_stop_button_sets_flag(self, qtbot):
        _state.stop_requested = False
        panel = AdaptiveExperimentPanel()
        qtbot.addWidget(panel)
        buttons = panel.findChildren(QPushButton)
        stop_btn = next(b for b in buttons if b.text() == "Stop")
        stop_btn.click()
        assert _state.stop_requested is True

    def test_iteration_updates_label(self, qtbot):
        panel = AdaptiveExperimentPanel()
        qtbot.addWidget(panel)
        _state.iteration_changed.emit(42)
        # The label's text should contain 42
        from PySide6.QtWidgets import QLabel
        labels = panel.findChildren(QLabel)
        texts = " ".join(lbl.text() for lbl in labels)
        assert "42" in texts


def _mock_device(**kwargs):
    """Create a MagicMock device compatible with bluesky's ancestry() walk."""
    m = MagicMock(**kwargs)
    m.parent = None  # must set after init — MagicMock reserves 'parent' kwarg
    return m


class TestAdaptiveExperimentPlan:
    """Drive the plan generator manually (no RunEngine) with mock IPC."""

    def _mock_ipc(self, messages_by_subject: dict[str, list[dict]]):
        """Build a mock IPCService that delivers queued messages on subscribe."""
        ipc = MagicMock()
        callbacks: dict[str, callable] = {}

        def fake_subscribe(subject, *, callback, main_thread=False):
            callbacks[subject] = callback
            # Deliver queued messages immediately
            for msg in messages_by_subject.get(subject, []):
                callback(subject, msg, None)
            sub = MagicMock()
            sub.unsubscribe = MagicMock()
            return sub

        ipc.subscribe.side_effect = fake_subscribe
        ipc.publish = MagicMock()
        return ipc, callbacks

    def test_plan_yields_open_and_close_run(self, qtbot, monkeypatch):
        """Minimal plan run: no targets -> times out quickly, yields open/close."""
        ipc, _ = self._mock_ipc({})
        monkeypatch.setattr("lucid.ipc.service.get_ipc_service", lambda: ipc)

        _state.stop_requested = False
        gen = adaptive_experiment(
            detectors=[_mock_device()],
            motors=[_mock_device()],

            timeout=0.1,
            poll_interval=0.01,
        )
        msgs = list(gen)
        # First msg is open_run, last is close_run
        commands = [m.command if hasattr(m, "command") else None for m in msgs]
        assert "open_run" in commands
        assert "close_run" in commands

    def test_plan_measures_targets(self, qtbot, monkeypatch):
        """Plan consumes a targets message and issues mv + trigger_and_read."""
        motors = [_mock_device(name="motor1"), _mock_device(name="motor2")]
        ipc, _ = self._mock_ipc({
            "tsuchinoko.targets": [
                {"iteration": 1, "targets": [[10.0, 20.0]]},
            ],
        })
        monkeypatch.setattr("lucid.ipc.service.get_ipc_service", lambda: ipc)

        _state.stop_requested = False
        gen = adaptive_experiment(
            detectors=[_mock_device()],
            motors=motors,

            timeout=0.3,
            poll_interval=0.01,
        )
        msgs = list(gen)
        commands = [m.command if hasattr(m, "command") else None for m in msgs]
        assert "set" in commands  # mv emits "set"
        assert "read" in commands  # trigger_and_read emits "read"

    def test_plan_publishes_measured_per_point_by_default(self, qtbot, monkeypatch):
        ipc, _ = self._mock_ipc({
            "tsuchinoko.targets": [
                {"iteration": 1, "targets": [[10.0, 20.0], [30.0, 40.0]]},
            ],
        })
        monkeypatch.setattr("lucid.ipc.service.get_ipc_service", lambda: ipc)
        monkeypatch.setattr(
            "lucid.acquire.plans.adaptive._get_lucid_prefix",
            lambda: "test.lucid",
        )

        _state.stop_requested = False
        gen = adaptive_experiment(
            detectors=[_mock_device()],
            motors=[_mock_device(), _mock_device()],
            exhaust_first=False,
            timeout=0.3,
            poll_interval=0.01,
        )
        list(gen)

        # exhaust_first=False means one publish per target (2 total for this batch)
        publishes = [
            call for call in ipc.publish.call_args_list
            if call.args[0] == "test.lucid.adaptive.measured"
        ]
        assert len(publishes) == 2

    def test_plan_exhaust_first_publishes_once_per_batch(self, qtbot, monkeypatch):
        ipc, _ = self._mock_ipc({
            "tsuchinoko.targets": [
                {"iteration": 1, "targets": [[10.0, 20.0], [30.0, 40.0]]},
            ],
        })
        monkeypatch.setattr("lucid.ipc.service.get_ipc_service", lambda: ipc)
        monkeypatch.setattr(
            "lucid.acquire.plans.adaptive._get_lucid_prefix",
            lambda: "test.lucid",
        )

        _state.stop_requested = False
        gen = adaptive_experiment(
            detectors=[_mock_device()],
            motors=[_mock_device(), _mock_device()],
            exhaust_first=True,
            timeout=0.3,
            poll_interval=0.01,
        )
        list(gen)

        publishes = [
            call for call in ipc.publish.call_args_list
            if call.args[0] == "test.lucid.adaptive.measured"
        ]
        assert len(publishes) == 1
        assert publishes[0].args[1]["n_new_points"] == 2

    def test_plan_stops_on_stop_requested(self, qtbot, monkeypatch):
        """Set stop flag via a message callback so it fires after reset."""
        ipc, callbacks = self._mock_ipc({})
        monkeypatch.setattr("lucid.ipc.service.get_ipc_service", lambda: ipc)

        # Override subscribe so that after subscribe completes, we set stop
        original_side_effect = ipc.subscribe.side_effect

        def subscribe_then_stop(subject, *, callback, main_thread=False):
            result = original_side_effect(subject, callback=callback, main_thread=main_thread)
            # After the plan subscribes, set stop so the while loop exits immediately
            _state.stop_requested = True
            return result

        ipc.subscribe.side_effect = subscribe_then_stop

        _state.stop_requested = False
        gen = adaptive_experiment(
            detectors=[_mock_device()],
            motors=[_mock_device()],

            timeout=5.0,
            poll_interval=0.01,
        )
        start = time.monotonic()
        msgs = list(gen)
        elapsed = time.monotonic() - start

        commands = [m.command if hasattr(m, "command") else None for m in msgs]
        assert "open_run" in commands
        assert "close_run" in commands
        # Should exit quickly without hitting the timeout
        assert elapsed < 2.0

    def test_plan_resets_state_at_start(self, qtbot, monkeypatch):
        ipc, _ = self._mock_ipc({})
        monkeypatch.setattr("lucid.ipc.service.get_ipc_service", lambda: ipc)

        # Preset state as if a previous run left it dirty
        _state.stop_requested = False  # but we want a timely exit
        _state.current_iteration = 99

        gen = adaptive_experiment(
            detectors=[_mock_device()],
            motors=[_mock_device()],

            timeout=0.1,
            poll_interval=0.01,
        )
        list(gen)
        # After the plan ran, current_iteration was reset to 0 (then possibly
        # incremented). Since no targets arrived, it should still be 0.
        assert _state.current_iteration == 0
