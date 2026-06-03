# Plan UI Framework + Adaptive Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a general plan-UI framework to Lightfall so plans can embed their own runtime controls as tabs in the Plans panel, and use it to build the `adaptive_experiment` plan that coordinates with Tsuchinoko over NATS.

**Architecture:** Three new files (plan_ui.py framework, nats_bridge.py, adaptive.py). Plans opt in via `@plan_with_ui(UIClass)` decorator. Plan and UI share state via module-level `PlanState` instance (no ContextVar/injection). Plans panel wraps its existing content in a QTabWidget with `tabBarAutoHide(True)` — running plan UI appears as a tab. The adaptive plan polls a thread-safe queue populated by NATS subscriptions, yielding `bps.sleep()` between polls.

**Tech Stack:** PySide6, bluesky, nats-py (via lightfall.ipc), pytest, pytest-qt, pytest-asyncio

**Design spec:** `docs/superpowers/specs/2026-04-12-plan-ui-adaptive-plan-design.md`

---

## File Structure

### New files
| File | Responsibility |
|------|----------------|
| `src/lightfall/acquire/plan_ui.py` | `plan_with_ui` decorator, `PlanState` base, `PlanUI` base widget |
| `src/lightfall/acquire/nats_bridge.py` | `NATSPlanBridge` — NATS subscription → queue bridge |
| `src/lightfall/acquire/plans/adaptive.py` | `AdaptivePlanState`, `_state` singleton, `AdaptiveExperimentPanel`, `adaptive_experiment` plan |
| `tests/acquire/__init__.py` | Empty — package marker |
| `tests/acquire/test_plan_ui_framework.py` | Unit tests for framework |
| `tests/acquire/test_nats_plan_bridge.py` | Unit tests for NATS bridge (mock IPCService) |
| `tests/acquire/test_adaptive_plan.py` | Unit tests for adaptive plan (drive generator manually) |
| `tests/acquire/test_plan_ui_integration.py` | Plans panel tab integration test (qtbot) |

### Modified files
| File | Changes |
|------|---------|
| `src/lightfall/ui/panels/bluesky_panel.py` | Wrap content in QTabWidget, add plan-UI tab lifecycle |
| `src/lightfall/acquire/plans/ncs_plans.py` | Register `adaptive_experiment` in `register_ncs_plans` |

---

## Task 1: Plan UI framework

**Files:**
- Create: `src/lightfall/acquire/plan_ui.py`
- Create: `tests/acquire/__init__.py` (empty file)
- Create: `tests/acquire/test_plan_ui_framework.py`

The minimal framework: decorator attaches UI class to a plan function, `PlanState` base class holds shared flags and signals, `PlanUI` is a thin base for UI widgets.

- [ ] **Step 1: Create the empty package marker**

Create `tests/acquire/__init__.py` as an empty file.

- [ ] **Step 2: Write failing tests**

Create `tests/acquire/test_plan_ui_framework.py`:

```python
"""Tests for the plan UI framework."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from lightfall.acquire.plan_ui import PlanState, PlanUI, plan_with_ui


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
        from lightfall.acquire.plan_ui import get_plan_ui_class

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
```

- [ ] **Step 3: Run tests — verify fail**

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && .venv/Scripts/python -m pytest tests/acquire/test_plan_ui_framework.py -v
```

Expected: `ModuleNotFoundError: No module named 'lightfall.acquire.plan_ui'`

- [ ] **Step 4: Implement the framework**

Create `src/lightfall/acquire/plan_ui.py`:

```python
"""Plan UI framework — plans can embed runtime UI widgets.

Plans opt in by decorating with @plan_with_ui(UIClass). When Lightfall's
Plans panel submits such a plan, it creates a UI widget and shows it as
a tab in the panel. The plan and UI share state via a module-level
PlanState instance that the plan module defines.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

_PlanFunc = TypeVar("_PlanFunc", bound=Callable[..., Any])


class PlanState(QObject):
    """Shared state between a running plan and its UI.

    Plan subclasses add their own signals and attributes. The plan module
    keeps a module-level instance that both the plan function and the UI
    widget reference directly.

    Thread safety:
      - Qt signals are thread-safe across threads.
      - Simple attribute reads/writes on primitives are GIL-safe.
      - Plans reset their state explicitly at the start of each run.
    """

    status_changed = Signal(str)

    # Defaults — subclasses inherit these
    stop_requested: bool = False
    pause_requested: bool = False

    def __init__(self) -> None:
        super().__init__()


class PlanUI(QWidget):
    """Base class for plan UI widgets.

    Subclasses build their UI in __init__ and reference the module-level
    PlanState instance directly (no framework injection needed).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)


def plan_with_ui(ui_class: type[PlanUI]) -> Callable[[_PlanFunc], _PlanFunc]:
    """Decorator: attach a UI class to a plan function.

    The Plans panel checks for the _plan_ui_class attribute when a plan is
    submitted. If present, the panel instantiates the UI and shows it as
    a tab while the plan runs.

    Example:
        >>> class MyPanel(PlanUI):
        ...     pass
        >>> @plan_with_ui(MyPanel)
        ... def my_plan(detectors):
        ...     yield from bps.count(detectors)
    """

    def decorator(plan_func: _PlanFunc) -> _PlanFunc:
        plan_func._plan_ui_class = ui_class  # type: ignore[attr-defined]
        return plan_func

    return decorator


def get_plan_ui_class(plan_func: Callable[..., Any]) -> type[PlanUI] | None:
    """Return the UI class attached to a plan, or None if it has none."""
    return getattr(plan_func, "_plan_ui_class", None)
```

- [ ] **Step 5: Run tests — verify pass**

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && .venv/Scripts/python -m pytest tests/acquire/test_plan_ui_framework.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lightfall/acquire/plan_ui.py tests/acquire/__init__.py tests/acquire/test_plan_ui_framework.py
git commit -m "feat: add plan UI framework (decorator + PlanState + PlanUI)"
```

---

## Task 2: NATS plan bridge

**Files:**
- Create: `src/lightfall/acquire/nats_bridge.py`
- Create: `tests/acquire/test_nats_plan_bridge.py`

Bridges async NATS subscriptions into poll-able queues the plan can drain from a sync generator context.

- [ ] **Step 1: Write failing tests**

Create `tests/acquire/test_nats_plan_bridge.py`:

```python
"""Tests for NATSPlanBridge using a mock IPCService."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.acquire.nats_bridge import NATSPlanBridge


@pytest.fixture
def mock_ipc():
    ipc = MagicMock()
    # subscribe returns an object with unsubscribe() method
    def make_sub(*args, **kwargs):
        sub = MagicMock()
        sub.unsubscribe = MagicMock()
        return sub
    ipc.subscribe.side_effect = make_sub
    ipc.publish = MagicMock()
    return ipc


class TestNATSPlanBridge:
    def test_subscribe_creates_queue(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("tsuchinoko.targets")
        assert "tsuchinoko.targets" in bridge._queues
        mock_ipc.subscribe.assert_called_once()

    def test_subscribe_idempotent(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("tsuchinoko.targets")
        bridge.subscribe("tsuchinoko.targets")
        assert mock_ipc.subscribe.call_count == 1

    def test_try_get_empty_returns_none(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("tsuchinoko.targets")
        assert bridge.try_get("tsuchinoko.targets") is None

    def test_try_get_unknown_subject_returns_none(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        assert bridge.try_get("nonexistent") is None

    def test_callback_puts_into_queue(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("tsuchinoko.targets")
        # Capture the callback passed to ipc.subscribe
        call_kwargs = mock_ipc.subscribe.call_args
        callback = call_kwargs.kwargs.get("callback") or call_kwargs.args[1]

        # Simulate an incoming message
        callback("tsuchinoko.targets", {"iteration": 1}, None)
        msg = bridge.try_get("tsuchinoko.targets")
        assert msg == {"iteration": 1}
        # Second call — queue is empty again
        assert bridge.try_get("tsuchinoko.targets") is None

    def test_publish_forwards_to_ipc(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.publish("lightfall.adaptive.measured", {"iteration": 1})
        mock_ipc.publish.assert_called_once_with(
            "lightfall.adaptive.measured", {"iteration": 1}
        )

    def test_cleanup_unsubscribes(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("a")
        bridge.subscribe("b")
        bridge.cleanup()
        assert len(bridge._subscriptions) == 0
        assert len(bridge._queues) == 0

    def test_cleanup_tolerates_unsubscribe_error(self, mock_ipc):
        bridge = NATSPlanBridge(mock_ipc)
        bridge.subscribe("a")
        bridge._subscriptions[0].unsubscribe.side_effect = Exception("boom")
        # Should not raise
        bridge.cleanup()
```

- [ ] **Step 2: Run tests — verify fail**

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && .venv/Scripts/python -m pytest tests/acquire/test_nats_plan_bridge.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement the bridge**

Create `src/lightfall/acquire/nats_bridge.py`:

```python
"""NATS ↔ bluesky plan bridge.

Translates async NATS subscriptions into poll-able queues a generator-based
plan can drain via try_get(), yielding bps.sleep() between polls.
"""

from __future__ import annotations

import queue
from typing import Any


class NATSPlanBridge:
    """Buffers NATS messages into thread-safe queues for synchronous plan polling.

    The bridge subscribes to NATS subjects on behalf of a running plan. Incoming
    messages land in an internal Queue. The plan drains the queue with
    try_get() and yields bps.sleep() between polls, keeping the RunEngine
    responsive.
    """

    def __init__(self, ipc_service: Any) -> None:
        self._ipc = ipc_service
        self._queues: dict[str, queue.Queue] = {}
        self._subscriptions: list[Any] = []

    def subscribe(self, subject: str) -> None:
        """Subscribe to a subject; incoming messages queue for try_get()."""
        if subject in self._queues:
            return
        q: queue.Queue = queue.Queue()
        self._queues[subject] = q

        def handler(msg_subject: str, data: dict, reply: str | None) -> None:
            q.put(data)

        sub = self._ipc.subscribe(subject, callback=handler, main_thread=False)
        self._subscriptions.append(sub)

    def try_get(self, subject: str) -> dict | None:
        """Non-blocking: return next message or None if queue is empty."""
        q = self._queues.get(subject)
        if q is None:
            return None
        try:
            return q.get_nowait()
        except queue.Empty:
            return None

    def publish(self, subject: str, payload: dict) -> None:
        """Publish a message via the IPC service. Fire-and-forget."""
        self._ipc.publish(subject, payload)

    def cleanup(self) -> None:
        """Unsubscribe all subscriptions. Call from the plan's finally block."""
        for sub in self._subscriptions:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        self._subscriptions.clear()
        self._queues.clear()
```

- [ ] **Step 4: Run tests — verify pass**

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && .venv/Scripts/python -m pytest tests/acquire/test_nats_plan_bridge.py -v
```

Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/acquire/nats_bridge.py tests/acquire/test_nats_plan_bridge.py
git commit -m "feat: add NATSPlanBridge for sync-plan ↔ async-NATS bridging"
```

---

## Task 3: Adaptive plan module (state + UI + plan)

**Files:**
- Create: `src/lightfall/acquire/plans/adaptive.py`
- Create: `tests/acquire/test_adaptive_plan.py`

The adaptive plan module — contains `AdaptivePlanState`, module-level `_state` singleton, `AdaptiveExperimentPanel` widget, and `adaptive_experiment` plan function.

- [ ] **Step 1: Write failing tests**

Create `tests/acquire/test_adaptive_plan.py`:

```python
"""Tests for the adaptive experiment plan (module-level state + plan logic)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QPushButton

from lightfall.acquire.plans.adaptive import (
    AdaptiveExperimentPanel,
    AdaptivePlanState,
    _state,
    adaptive_experiment,
)


class TestAdaptivePlanState:
    def test_subclasses_plan_state(self, qtbot):
        from lightfall.acquire.plan_ui import PlanState
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


class TestAdaptiveExperimentPlan:
    """Drive the plan generator manually (no RunEngine) with mock IPC."""

    def _mock_ipc(self, messages_by_subject: dict[str, list[dict]]):
        """Build a mock IPCService that delivers queued messages on subscribe."""
        from lightfall.acquire.nats_bridge import NATSPlanBridge

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
        """Minimal plan run: no targets → times out quickly, yields open/close."""
        ipc, _ = self._mock_ipc({})
        monkeypatch.setattr("lightfall.ipc.service.get_ipc_service", lambda: ipc)

        _state.stop_requested = False
        gen = adaptive_experiment(
            detectors=[MagicMock()],
            motors=[MagicMock()],
            experiment_id="test",
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
        motors = [MagicMock(name="motor1"), MagicMock(name="motor2")]
        ipc, _ = self._mock_ipc({
            "tsuchinoko.targets": [
                {"iteration": 1, "targets": [[10.0, 20.0]]},
            ],
        })
        monkeypatch.setattr("lightfall.ipc.service.get_ipc_service", lambda: ipc)

        _state.stop_requested = False
        gen = adaptive_experiment(
            detectors=[MagicMock()],
            motors=motors,
            experiment_id="test",
            timeout=0.3,
            poll_interval=0.01,
        )
        msgs = list(gen)
        commands = [m.command if hasattr(m, "command") else None for m in msgs]
        assert "set" in commands  # mv emits "set"
        assert "trigger" in commands or "read" in commands

    def test_plan_publishes_measured_per_point_by_default(self, qtbot, monkeypatch):
        ipc, _ = self._mock_ipc({
            "tsuchinoko.targets": [
                {"iteration": 1, "targets": [[10.0, 20.0], [30.0, 40.0]]},
            ],
        })
        monkeypatch.setattr("lightfall.ipc.service.get_ipc_service", lambda: ipc)

        _state.stop_requested = False
        gen = adaptive_experiment(
            detectors=[MagicMock()],
            motors=[MagicMock(), MagicMock()],
            experiment_id="test",
            lightfall_prefix="test.lightfall",
            exhaust_first=False,
            timeout=0.3,
            poll_interval=0.01,
        )
        list(gen)

        # exhaust_first=False means one publish per target (2 total for this batch)
        publishes = [
            call for call in ipc.publish.call_args_list
            if call.args[0] == "test.lightfall.adaptive.measured"
        ]
        assert len(publishes) == 2

    def test_plan_exhaust_first_publishes_once_per_batch(self, qtbot, monkeypatch):
        ipc, _ = self._mock_ipc({
            "tsuchinoko.targets": [
                {"iteration": 1, "targets": [[10.0, 20.0], [30.0, 40.0]]},
            ],
        })
        monkeypatch.setattr("lightfall.ipc.service.get_ipc_service", lambda: ipc)

        _state.stop_requested = False
        gen = adaptive_experiment(
            detectors=[MagicMock()],
            motors=[MagicMock(), MagicMock()],
            experiment_id="test",
            lightfall_prefix="test.lightfall",
            exhaust_first=True,
            timeout=0.3,
            poll_interval=0.01,
        )
        list(gen)

        publishes = [
            call for call in ipc.publish.call_args_list
            if call.args[0] == "test.lightfall.adaptive.measured"
        ]
        assert len(publishes) == 1
        assert publishes[0].args[1]["n_new_points"] == 2

    def test_plan_stops_on_stop_requested(self, qtbot, monkeypatch):
        ipc, _ = self._mock_ipc({})
        monkeypatch.setattr("lightfall.ipc.service.get_ipc_service", lambda: ipc)

        _state.stop_requested = True  # preset stop flag
        gen = adaptive_experiment(
            detectors=[MagicMock()],
            motors=[MagicMock()],
            experiment_id="test",
            timeout=5.0,
            poll_interval=0.01,
        )
        msgs = list(gen)
        commands = [m.command if hasattr(m, "command") else None for m in msgs]
        assert "open_run" in commands
        assert "close_run" in commands
        # Should exit quickly without hitting the timeout

    def test_plan_resets_state_at_start(self, qtbot, monkeypatch):
        ipc, _ = self._mock_ipc({})
        monkeypatch.setattr("lightfall.ipc.service.get_ipc_service", lambda: ipc)

        # Preset state as if a previous run left it dirty
        _state.stop_requested = False  # but we want a timely exit
        _state.current_iteration = 99

        gen = adaptive_experiment(
            detectors=[MagicMock()],
            motors=[MagicMock()],
            experiment_id="test",
            timeout=0.1,
            poll_interval=0.01,
        )
        list(gen)
        # After the plan ran, current_iteration was reset to 0 (then possibly
        # incremented). Since no targets arrived, it should still be 0.
        assert _state.current_iteration == 0
```

- [ ] **Step 2: Run tests — verify fail**

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && .venv/Scripts/python -m pytest tests/acquire/test_adaptive_plan.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement adaptive module**

Create `src/lightfall/acquire/plans/adaptive.py`:

```python
"""Adaptive experiment plan — coordinates with Tsuchinoko over NATS.

Tsuchinoko sends measurement targets via NATS; this plan measures them
and signals back when each point is done. The plan opens a bluesky run at
start and closes it when stopped (via UI stop button or timeout).

See: docs/superpowers/specs/2026-04-12-plan-ui-adaptive-plan-design.md
"""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any

from bluesky import plan_stubs as bps
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from lightfall.acquire.plan_ui import PlanState, PlanUI, plan_with_ui
from lightfall.utils.logging import logger


class AdaptivePlanState(PlanState):
    """State specific to the adaptive experiment plan."""

    iteration_changed = Signal(int)
    targets_received = Signal(int)

    current_iteration: int = 0


# Module-level singleton — shared between plan and UI
_state = AdaptivePlanState()


class AdaptiveExperimentPanel(PlanUI):
    """UI for the adaptive experiment plan.

    Reads state from the module-level _state instance.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # Status labels
        self._iteration_label = QLabel("Iteration: 0")
        self._targets_label = QLabel("Last batch: 0")
        self._status_label = QLabel("Status: waiting for targets")

        # Control buttons
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop_clicked)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setCheckable(True)
        self._pause_btn.toggled.connect(self._on_pause_toggled)

        # Layout
        button_row = QHBoxLayout()
        button_row.addWidget(self._stop_btn)
        button_row.addWidget(self._pause_btn)
        button_row.addStretch()

        self._layout.addWidget(self._iteration_label)
        self._layout.addWidget(self._targets_label)
        self._layout.addWidget(self._status_label)
        self._layout.addLayout(button_row)
        self._layout.addStretch()

        # Connect to module-level state
        _state.iteration_changed.connect(self._on_iteration_changed)
        _state.targets_received.connect(self._on_targets_received)
        _state.status_changed.connect(self._on_status_changed)

    def _on_iteration_changed(self, i: int) -> None:
        self._iteration_label.setText(f"Iteration: {i}")

    def _on_targets_received(self, n: int) -> None:
        self._targets_label.setText(f"Last batch: {n}")

    def _on_status_changed(self, msg: str) -> None:
        self._status_label.setText(f"Status: {msg}")

    def _on_stop_clicked(self) -> None:
        _state.stop_requested = True
        self._stop_btn.setEnabled(False)
        self._status_label.setText("Status: stopping…")

    def _on_pause_toggled(self, checked: bool) -> None:
        _state.pause_requested = checked
        self._pause_btn.setText("Resume" if checked else "Pause")


@plan_with_ui(AdaptiveExperimentPanel)
def adaptive_experiment(
    detectors: list,
    motors: list,
    experiment_id: str,
    lightfall_prefix: str = "als.7011",
    exhaust_first: bool = False,
    timeout: float = 300.0,
    poll_interval: float = 0.1,
) -> Generator[Any, Any, Any]:
    """GP-driven adaptive measurement plan.

    Waits for measurement targets from Tsuchinoko via NATS, executes them,
    and signals back when each point is measured. Opens a single bluesky
    Run for the entire experiment.

    Args:
        detectors: Detectors to read at each target.
        motors: Motors to move. Target tuples align with motor order.
        experiment_id: Tsuchinoko experiment UUID (embedded in start doc).
        lightfall_prefix: NATS topic prefix for this Lightfall instance.
        exhaust_first: If True, measure all targets in a batch before
            publishing adaptive.measured. If False (default), publish after
            each measurement so Tsuchinoko can update its GP per-point.
        timeout: Seconds to wait for new targets before aborting.
        poll_interval: Seconds between NATS queue polls.

    Yields:
        Bluesky plan messages.
    """
    from lightfall.acquire.nats_bridge import NATSPlanBridge
    from lightfall.ipc.service import get_ipc_service

    # Reset module-level state for this run
    _state.stop_requested = False
    _state.pause_requested = False
    _state.current_iteration = 0

    ipc = get_ipc_service()
    if ipc is None:
        raise RuntimeError("NATS not available — adaptive plan requires IPC")

    bridge = NATSPlanBridge(ipc)
    bridge.subscribe("tsuchinoko.targets")

    try:
        md = {"tsuchinoko": {"experiment_id": experiment_id}}
        yield from bps.open_run(md=md)

        deadline = time.monotonic() + timeout

        while not _state.stop_requested:
            while _state.pause_requested and not _state.stop_requested:
                yield from bps.sleep(poll_interval)

            msg = bridge.try_get("tsuchinoko.targets")
            if msg is None:
                if time.monotonic() > deadline:
                    _state.status_changed.emit("Timeout waiting for targets")
                    logger.warning("adaptive_experiment: timeout")
                    break
                yield from bps.sleep(poll_interval)
                continue

            targets = msg.get("targets", [])
            iteration = msg.get("iteration", _state.current_iteration + 1)
            _state.current_iteration = iteration
            _state.iteration_changed.emit(iteration)
            _state.targets_received.emit(len(targets))

            for target in targets:
                if _state.stop_requested:
                    break
                # Interleave motors and target values for bps.mv
                args: list[Any] = []
                for motor, value in zip(motors, target):
                    args.append(motor)
                    args.append(value)
                yield from bps.mv(*args)
                yield from bps.trigger_and_read(detectors, name="primary")

                if not exhaust_first:
                    bridge.publish(f"{lightfall_prefix}.adaptive.measured", {
                        "iteration": iteration,
                        "n_new_points": 1,
                    })

            if exhaust_first and not _state.stop_requested:
                bridge.publish(f"{lightfall_prefix}.adaptive.measured", {
                    "iteration": iteration,
                    "n_new_points": len(targets),
                })

            deadline = time.monotonic() + timeout

        yield from bps.close_run()
    finally:
        bridge.cleanup()
```

- [ ] **Step 4: Run tests — verify pass**

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && .venv/Scripts/python -m pytest tests/acquire/test_adaptive_plan.py -v
```

Expected: All 11 tests PASS. Some tests may have slight variations in message commands depending on bluesky version — if any fail on exact command names, the implementation may need minor adjustments (e.g., bluesky's `mv` may yield `"set"` + `"wait"` messages).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall/acquire/plans/adaptive.py tests/acquire/test_adaptive_plan.py
git commit -m "feat: add adaptive_experiment plan with UI and NATS coordination"
```

---

## Task 4: Plans panel QTabWidget integration

**Files:**
- Modify: `src/lightfall/ui/panels/bluesky_panel.py`
- Create: `tests/acquire/test_plan_ui_integration.py`

Wrap the existing panel content (toolbar + selector + config splitter) so it lives in "Tab 1". When a plan with `_plan_ui_class` is submitted, create the UI widget and add it as a new tab. When the engine finishes, remove the tab.

- [ ] **Step 1: Write failing integration test**

Create `tests/acquire/test_plan_ui_integration.py`:

```python
"""Integration tests for plan UI lifecycle in the BlueskyPanel."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QTabWidget

from lightfall.acquire.plan_ui import PlanUI, plan_with_ui


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
        from lightfall.ui.panels.bluesky_panel import BlueskyPanel

        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        # Find the QTabWidget
        tab_widget = panel.findChild(QTabWidget)
        assert tab_widget is not None
        # Should have tab bar auto-hide enabled
        assert tab_widget.tabBarAutoHide() is True

    def test_initial_one_tab(self, qtbot):
        from lightfall.ui.panels.bluesky_panel import BlueskyPanel

        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)
        assert tab_widget.count() == 1  # just the selector tab

    def test_adds_tab_for_plan_with_ui(self, qtbot):
        from lightfall.acquire.plans import PlanInfo
        from lightfall.ui.panels.bluesky_panel import BlueskyPanel

        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        # Create a PlanInfo wrapping our dummy plan
        plan_info = PlanInfo.from_function(
            "dummy_plan_with_ui", dummy_plan_with_ui, category="test"
        )

        # Mock engine for submission
        panel._engine = MagicMock()
        panel._engine.__call__ = MagicMock()

        panel._on_run_requested(plan_info, {})

        # Should now have 2 tabs
        assert tab_widget.count() == 2
        # Latest tab should contain a DummyPlanUI
        ui_widget = tab_widget.widget(1)
        assert isinstance(ui_widget, DummyPlanUI)

    def test_no_tab_for_plan_without_ui(self, qtbot):
        from lightfall.acquire.plans import PlanInfo
        from lightfall.ui.panels.bluesky_panel import BlueskyPanel

        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        plan_info = PlanInfo.from_function(
            "dummy_plan_no_ui", dummy_plan_no_ui, category="test"
        )

        panel._engine = MagicMock()
        panel._on_run_requested(plan_info, {})

        # Still just one tab
        assert tab_widget.count() == 1

    def test_removes_tab_on_finish(self, qtbot):
        from lightfall.acquire.plans import PlanInfo
        from lightfall.ui.panels.bluesky_panel import BlueskyPanel

        panel = BlueskyPanel()
        qtbot.addWidget(panel)
        tab_widget = panel.findChild(QTabWidget)

        plan_info = PlanInfo.from_function(
            "dummy_plan_with_ui", dummy_plan_with_ui, category="test"
        )
        panel._engine = MagicMock()
        panel._on_run_requested(plan_info, {})
        assert tab_widget.count() == 2

        # Simulate finish
        panel._on_plan_ui_finished()
        assert tab_widget.count() == 1
```

- [ ] **Step 2: Run test — verify fail**

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && .venv/Scripts/python -m pytest tests/acquire/test_plan_ui_integration.py -v
```

Expected: AttributeError or AssertionError — panel doesn't use QTabWidget yet.

- [ ] **Step 3: Refactor BlueskyPanel `_setup_ui` to use QTabWidget**

In `src/lightfall/ui/panels/bluesky_panel.py`, modify `_setup_ui` method. Replace the existing body (around lines 108-133) with:

```python
    def _setup_ui(self) -> None:
        """Set up the panel UI."""
        # Toolbar for plan actions
        self._toolbar = QToolBar()
        self._toolbar.setMovable(False)
        self._setup_toolbar()
        self._layout.addWidget(self._toolbar)

        # QTabWidget for plan selector + running plan UIs
        self._tab_widget = QTabWidget()
        self._tab_widget.setTabBarAutoHide(True)

        # Tab 1: plan selector + config (existing content)
        selector_container = QWidget()
        selector_layout = QVBoxLayout(selector_container)
        selector_layout.setContentsMargins(0, 0, 0, 0)

        self._plan_selector = PlanSelectorWidget()
        self._plan_selector.plan_selected.connect(self._on_plan_selected)

        self._plan_config = PlanConfigWidget()
        self._plan_config.run_requested.connect(self._on_run_requested)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._plan_selector)
        splitter.addWidget(self._plan_config)
        splitter.setSizes([300, 200])

        selector_layout.addWidget(splitter)
        self._tab_widget.addTab(selector_container, "Plans")

        self._layout.addWidget(self._tab_widget)

        # Running plan UI state
        self._running_plan_ui: PlanUI | None = None
        self._running_plan_tab_index: int = -1

        # Auto-configure with RunEngine and PlanRegistry singletons
        self._auto_configure()
```

Add these imports to the top of the file:

```python
from PySide6.QtWidgets import (
    QDialog,
    QSplitter,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from lightfall.acquire.plan_ui import PlanUI, get_plan_ui_class
```

- [ ] **Step 4: Add plan-UI lifecycle methods**

In `src/lightfall/ui/panels/bluesky_panel.py`, modify `_on_run_requested` to add UI tab creation. Replace the method (around line 365) with:

```python
    @Slot(object, dict)
    def _on_run_requested(self, plan_info: PlanInfo, kwargs: dict) -> None:
        """Handle run request from config widget.

        Args:
            plan_info: Plan to run.
            kwargs: Parameter values.
        """
        if self._engine is None:
            logger.error("No Engine configured")
            return

        try:
            resolved_kwargs = self._resolve_device_kwargs(plan_info, kwargs)

            # Check for plan UI and create tab before submitting
            self._maybe_create_plan_ui(plan_info)

            plan = plan_info.func(**resolved_kwargs)
            self._engine(plan)
            self._current_plan_name = plan_info.name

            logger.info(f"Submitted plan: {plan_info.name}")
        except Exception as e:
            logger.error(f"Failed to run plan {plan_info.name}: {e}")
            self._on_plan_ui_finished()  # cleanup on error

    def _maybe_create_plan_ui(self, plan_info: PlanInfo) -> None:
        """If the plan has a _plan_ui_class, create a tab for it."""
        ui_class = get_plan_ui_class(plan_info.func)
        if ui_class is None:
            return

        # Remove any existing running plan UI first (one at a time)
        if self._running_plan_ui is not None:
            self._on_plan_ui_finished()

        ui = ui_class()
        self._running_plan_ui = ui
        self._running_plan_tab_index = self._tab_widget.addTab(
            ui, f"Running: {plan_info.name}"
        )
        self._tab_widget.setCurrentIndex(self._running_plan_tab_index)

    def _on_plan_ui_finished(self) -> None:
        """Remove the running plan UI tab, if any."""
        if self._running_plan_ui is None:
            return
        if self._running_plan_tab_index >= 0:
            self._tab_widget.removeTab(self._running_plan_tab_index)
        self._running_plan_ui.deleteLater()
        self._running_plan_ui = None
        self._running_plan_tab_index = -1
```

- [ ] **Step 5: Wire `_on_plan_ui_finished` into engine signals**

In `set_engine`, after `engine.sigFinish.connect(self._on_run_finish)`, add:

```python
        engine.sigFinish.connect(self._on_plan_ui_finished)
        engine.sigAbort.connect(self._on_plan_ui_finished)
        engine.sigException.connect(lambda _exc: self._on_plan_ui_finished())
```

(Find the `set_engine` method, around line 252, and add the three connections after the existing `engine.sigFinish.connect(...)`.)

- [ ] **Step 6: Run tests — verify pass**

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && .venv/Scripts/python -m pytest tests/acquire/test_plan_ui_integration.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/lightfall/ui/panels/bluesky_panel.py tests/acquire/test_plan_ui_integration.py
git commit -m "feat: Plans panel shows running plan UI as auto-hiding tab"
```

---

## Task 5: Register adaptive_experiment in the plan registry

**Files:**
- Modify: `src/lightfall/acquire/plans/ncs_plans.py`

Add the adaptive plan to the default registry so it appears in Lightfall's plan selector.

- [ ] **Step 1: Find the plan registration**

Open `src/lightfall/acquire/plans/ncs_plans.py` and find the `register_ncs_plans` function (grep for it to locate).

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && grep -n "register_ncs_plans" src/lightfall/acquire/plans/ncs_plans.py
```

- [ ] **Step 2: Add adaptive_experiment registration**

At the bottom of `register_ncs_plans`, before its `return` (or at the end if no return), add:

```python
    # Adaptive experiment (Tsuchinoko coordination)
    try:
        from lightfall.acquire.plans.adaptive import adaptive_experiment

        registry.register(
            "adaptive_experiment", adaptive_experiment, category="scan"
        )
    except ImportError as e:
        from lightfall.utils.logging import logger
        logger.debug(f"Could not register adaptive_experiment: {e}")
```

- [ ] **Step 3: Verify registration**

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && .venv/Scripts/python -c "from lightfall.acquire.plans.registry import create_default_registry; r = create_default_registry(); print(list(r.plan_names))"
```

Expected output: A list that includes `'adaptive_experiment'`.

- [ ] **Step 4: Commit**

```bash
git add src/lightfall/acquire/plans/ncs_plans.py
git commit -m "feat: register adaptive_experiment in default plan registry"
```

---

## Task 6: Final validation — run full test suite

**Files:** None new; run all new tests together.

- [ ] **Step 1: Run the Phase 4 test suite**

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && .venv/Scripts/python -m pytest tests/acquire/ -v
```

Expected: All tests pass. Total count: 7 (framework) + 8 (bridge) + 11 (adaptive plan) + 5 (integration) = 31 tests.

- [ ] **Step 2: Ensure no regressions in broader Lightfall tests**

```
cd /c/Users/rp/PycharmProjects/ncs-phase4 && .venv/Scripts/python -m pytest tests/ -v --timeout=60 -x -k "not slow"
```

Expected: All existing tests still pass. If any fail, they should be unrelated to our changes (Phase 4 only touches plan panel + adds new modules).

- [ ] **Step 3: Update spec status**

Edit `docs/superpowers/specs/2026-04-12-plan-ui-adaptive-plan-design.md` line 3:
Change `**Status:** Draft` to `**Status:** Implementation complete`.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-04-12-plan-ui-adaptive-plan-design.md
git commit -m "docs: mark plan UI + adaptive plan spec as implementation complete"
```

---

## Verification Checklist

- [ ] `plan_with_ui` decorator attaches `_plan_ui_class` to plan functions
- [ ] `PlanState` has `stop_requested`, `pause_requested`, and `status_changed` signal
- [ ] `NATSPlanBridge.subscribe/try_get/publish/cleanup` all work
- [ ] Adaptive plan yields `open_run` first and `close_run` last
- [ ] Adaptive plan stops immediately when `_state.stop_requested = True`
- [ ] Adaptive plan with `exhaust_first=False` publishes once per measurement
- [ ] Adaptive plan with `exhaust_first=True` publishes once per batch
- [ ] Adaptive plan resets `_state` at start of each run
- [ ] BlueskyPanel has a `QTabWidget` with `tabBarAutoHide(True)`
- [ ] Submitting a plan with `_plan_ui_class` adds a tab
- [ ] Submitting a plan without `_plan_ui_class` does not add a tab
- [ ] Plan finishing removes the tab (via `sigFinish` / `sigAbort` / `sigException`)
- [ ] `adaptive_experiment` appears in the default plan registry
