# Plan UI Framework + Adaptive Plan — Design Spec

**Status:** Draft
**Date:** 2026-04-12
**Authors:** Ron Pandolfi, Ayaka (Claude)
**Context:** Phase 4 of the Tsuchinoko rescope
  (`~/PycharmProjects/tsuchinoko-phase1/docs/design/2026-04-12-tsuchinoko-rescope.md`)

## Goal

Add two things to LUCID:

1. **Plan UI framework** — a general capability for LUCID plans to ship their own
   runtime UI, shown as a tab in the existing Plans panel while the plan is
   executing.

2. **Adaptive experiment plan** — a long-running bluesky plan that coordinates
   with Tsuchinoko over NATS to perform GP-driven adaptive measurements. First
   consumer of the plan UI framework.

GP visualization plugins are a separate spec, planned next.

## Design Principles

1. **Plan UI is a general feature.** Not specific to adaptive experiments.
   Any plan can opt in to having a UI by using the `@plan_with_ui` decorator.

2. **LUCID owns the "done" decision.** The plan's UI has a stop button that
   sets a flag the plan polls. Tsuchinoko learns the run ended via the existing
   `{prefix}.runs.complete` event.

3. **Plan signatures stay clean.** The UI-state injection uses `contextvars`
   so user-visible plan parameters aren't polluted with framework plumbing.
   LUCID's introspection/UI-generation systems work unchanged.

4. **Poll-based NATS bridging.** Bluesky plans are generator-based, not async.
   Rather than bolt an async primitive onto bluesky, the plan polls a
   thread-safe queue with short `bps.sleep()` yields. The RunEngine handles
   the sleep and keeps the event loop responsive.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                  Plans Panel (Bluesky)                    │
│                                                           │
│  ┌─────────────────────────────────────────────────┐     │
│  │  QTabWidget (tabBarAutoHide=True)                │     │
│  │                                                   │     │
│  │  [Plan Selector]  [Running: adaptive_experiment] │     │
│  │  ─────────────                                    │     │
│  │                                                   │     │
│  │   <selector UI>    <plan-specific UI>            │     │
│  │                                                   │     │
│  └─────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────┘
                            │
                     plan_state (ContextVar)
                            │
                            ▼
┌──────────────────────────────────────────────────────────┐
│             RunEngine (background thread)                │
│                                                           │
│   adaptive_experiment(motors, dets, ...):                │
│     state = get_plan_state()                             │
│     while not state.stop_requested:                      │
│         msg = bridge.try_get("tsuchinoko.targets")       │
│         if msg is None:                                  │
│             yield from bps.sleep(0.1)                    │
│             continue                                     │
│         ...                                              │
└──────────────────────────────────────────────────────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │  NATSPlanBridge │
                   │                 │
                   │ queue-based     │
                   │ subscriptions   │
                   └────────┬────────┘
                            │
                    NATS (IPCService)
```

## Plan UI Framework

### The decorator

```python
from lucid.acquire.plan_ui import plan_with_ui

@plan_with_ui(AdaptiveExperimentPanel)
def adaptive_experiment(detectors, motors, experiment_id):
    state = get_plan_state()
    ...
```

Implementation: the decorator attaches a `_plan_ui_class` attribute to the
plan function. LUCID's plan execution layer checks for this attribute.

```python
def plan_with_ui(ui_class):
    def decorator(plan_func):
        plan_func._plan_ui_class = ui_class
        return plan_func
    return decorator
```

### The state object

`PlanState` is a `QObject` subclass (for Qt signals) with simple attributes
for flags. Plans subclass it to add plan-specific state.

```python
from PySide6.QtCore import QObject, Signal

class PlanState(QObject):
    """Base class for shared state between a plan and its UI."""

    # Signals (plan → UI, thread-safe)
    status_changed = Signal(str)

    # UI → plan (simple atomic reads, GIL-safe)
    stop_requested: bool = False
    pause_requested: bool = False

    def __init__(self):
        super().__init__()


class AdaptivePlanState(PlanState):
    """State specific to the adaptive experiment plan."""

    # Additional signals
    iteration_changed = Signal(int)
    targets_received = Signal(int)  # n_targets

    current_iteration: int = 0
```

### State injection via ContextVar

```python
from contextvars import ContextVar

_current_plan_state: ContextVar[PlanState] = ContextVar("plan_state")


def get_plan_state() -> PlanState:
    """Get the plan state for the currently-running plan.

    Must be called from within a plan being executed by LUCID's
    plan runner. Raises LookupError if no plan is active.
    """
    return _current_plan_state.get()


def set_plan_state(state: PlanState) -> object:
    """Set the plan state. Called by LUCID's plan runner before invoking
    the plan generator. Returns a token for reset()."""
    return _current_plan_state.set(state)
```

LUCID's plan runner does something like:

```python
def _run_plan_with_state(plan_func, state, args, kwargs):
    token = set_plan_state(state)
    try:
        yield from plan_func(*args, **kwargs)
    finally:
        _current_plan_state.reset(token)
```

This wraps plan execution so the state is accessible via `get_plan_state()`
anywhere inside the plan.

### Plans panel integration

The existing Plans panel (in `bluesky_panel.py`) wraps its current content
in a `QTabWidget` with `tabBarAutoHide(True)`:

- **Tab 1:** Plan Selector (existing `PlanSelectorWidget` + param forms)
- **Tab 2+:** Running plan UI (created on demand)

Flow:

1. User clicks "Run" on a plan in the selector tab.
2. Panel inspects the plan function for `_plan_ui_class`.
3. If present:
   - Create `PlanState` instance (or the plan's subclass if declared).
   - Create UI widget with state as constructor arg.
   - Add widget as a new tab with title like `"Running: {plan_name}"`.
   - Switch to the new tab.
   - Pass the state to the plan runner for ContextVar injection.
4. Plan runs on the RunEngine's background thread.
5. When the plan finishes (success, abort, or error):
   - Remove the UI tab.
   - Switch back to the selector tab.
   - Disconnect state signals.

**No tab is created for plans without `_plan_ui_class`.**

### Plan UI base class

```python
from lucid.acquire.plan_ui import PlanUI

class AdaptiveExperimentPanel(PlanUI):
    """UI for the adaptive experiment plan."""

    state_class = AdaptivePlanState

    def __init__(self, state: AdaptivePlanState, parent=None):
        super().__init__(state, parent)
        # Build UI, connect signals
        self._iteration_label = QLabel("Iteration: 0")
        state.iteration_changed.connect(self._on_iteration_changed)

        self._stop_button = QPushButton("Stop")
        self._stop_button.clicked.connect(self._on_stop)

    def _on_iteration_changed(self, i: int):
        self._iteration_label.setText(f"Iteration: {i}")

    def _on_stop(self):
        self._state.stop_requested = True
```

`PlanUI` is a lightweight base class. All it requires is `__init__(state, parent)`.

## NATS Plan Bridge

Bluesky plans are generator-based, not async. The bridge translates async
NATS subscriptions into poll-able queues the plan can drain via `try_get()`.

```python
import queue
import threading
from typing import Any

from lucid.ipc.service import IPCService

class NATSPlanBridge:
    """Bridges NATS subscriptions into synchronous generator-based plans.

    On subscribe(), installs a NATS callback that puts messages into a
    thread-safe queue. The plan polls with try_get() and yields bps.sleep()
    between polls.
    """

    def __init__(self, ipc_service: IPCService):
        self._ipc = ipc_service
        self._queues: dict[str, queue.Queue] = {}
        self._subscriptions: list[Any] = []

    def subscribe(self, subject: str) -> None:
        """Subscribe to a subject, buffering messages in a queue."""
        if subject in self._queues:
            return
        q: queue.Queue = queue.Queue()
        self._queues[subject] = q

        def handler(msg_subject, data, reply):
            q.put(data)

        sub = self._ipc.subscribe(subject, callback=handler, main_thread=False)
        self._subscriptions.append(sub)

    def try_get(self, subject: str) -> dict | None:
        """Non-blocking: returns next message or None if queue is empty."""
        q = self._queues.get(subject)
        if q is None:
            return None
        try:
            return q.get_nowait()
        except queue.Empty:
            return None

    def publish(self, subject: str, payload: dict) -> None:
        """Publish a message. Fire-and-forget."""
        self._ipc.publish(subject, payload)

    def cleanup(self) -> None:
        """Unsubscribe all subscriptions. Call from plan's finally block."""
        for sub in self._subscriptions:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        self._subscriptions.clear()
        self._queues.clear()
```

**Thread safety:** the NATS callback runs on the IPC background thread, the
plan runs on the RunEngine thread. `queue.Queue` handles this. The plan
reads from the queue; the callback writes to it.

## Adaptive Experiment Plan

### Signature

```python
@plan_with_ui(AdaptiveExperimentPanel)
def adaptive_experiment(
    detectors: list[Detector],
    motors: list[Motor],
    experiment_id: str,
    lucid_prefix: str = "als.7011",
    exhaust_first: bool = False,
    timeout: float = 300.0,
    poll_interval: float = 0.1,
) -> Generator[Any, Any, Any]:
    """GP-driven adaptive measurement plan.

    Waits for measurement targets from Tsuchinoko via NATS, executes them,
    and signals back when each point is measured.

    Args:
        detectors: Detectors to read at each target.
        motors: Motors to move to target positions. Target order matches
            motor order.
        experiment_id: Tsuchinoko experiment UUID (embedded in start doc).
        lucid_prefix: NATS topic prefix for this LUCID instance.
        exhaust_first: If True, measure all targets in a batch before
            signaling back. If False (default), signal after each measurement
            so Tsuchinoko can update its GP per-point.
        timeout: Seconds to wait for new targets before aborting.
        poll_interval: Seconds between NATS queue polls.
    """
```

### Behavior

```python
def adaptive_experiment(detectors, motors, experiment_id,
                        lucid_prefix="als.7011", exhaust_first=False,
                        timeout=300.0, poll_interval=0.1):
    from lucid.acquire.plan_ui import get_plan_state
    from lucid.acquire.nats_bridge import NATSPlanBridge
    from lucid.ipc.service import get_ipc_service
    from bluesky import plan_stubs as bps
    import time

    state = get_plan_state()  # injected by plan runner
    ipc = get_ipc_service()
    if ipc is None:
        raise RuntimeError("NATS not available — adaptive plan requires IPC")

    bridge = NATSPlanBridge(ipc)
    bridge.subscribe("tsuchinoko.targets")

    try:
        md = {"tsuchinoko": {"experiment_id": experiment_id}}
        yield from bps.open_run(md=md)

        deadline = time.monotonic() + timeout

        while not state.stop_requested:
            while state.pause_requested and not state.stop_requested:
                yield from bps.sleep(poll_interval)

            msg = bridge.try_get("tsuchinoko.targets")
            if msg is None:
                if time.monotonic() > deadline:
                    state.status_changed.emit("Timeout waiting for targets")
                    break
                yield from bps.sleep(poll_interval)
                continue

            targets = msg.get("targets", [])
            iteration = msg.get("iteration", state.current_iteration + 1)
            state.current_iteration = iteration
            state.iteration_changed.emit(iteration)
            state.targets_received.emit(len(targets))

            for target in targets:
                if state.stop_requested:
                    break

                # Interleave motors and target values for mv
                args = [v for pair in zip(motors, target) for v in pair]
                yield from bps.mv(*args)
                yield from bps.trigger_and_read(detectors, name="primary")

                if not exhaust_first:
                    bridge.publish(f"{lucid_prefix}.adaptive.measured", {
                        "iteration": iteration,
                        "n_new_points": 1,
                    })

            if exhaust_first and not state.stop_requested:
                bridge.publish(f"{lucid_prefix}.adaptive.measured", {
                    "iteration": iteration,
                    "n_new_points": len(targets),
                })

            deadline = time.monotonic() + timeout

        yield from bps.close_run()
    finally:
        bridge.cleanup()
```

### State

```python
class AdaptivePlanState(PlanState):
    iteration_changed = Signal(int)
    targets_received = Signal(int)

    current_iteration: int = 0
```

### UI

The `AdaptiveExperimentPanel` widget shows:

- Current iteration
- Last batch size (from `targets_received`)
- Status string (from `status_changed`)
- **Stop** button — sets `state.stop_requested = True`
- **Pause/Resume** toggle — sets `state.pause_requested`

Simple Qt layout, no fancy visualizations. GP viz is separate (next spec).

## How Tsuchinoko Learns "Done"

The adaptive plan closes the run when stopped (either via UI or via timeout).
The RunEngine's normal lifecycle emits the stop document, which LUCID's IPC
layer publishes as `{prefix}.runs.complete`. Tsuchinoko is already subscribed
to this subject (per the rescope design doc's NATS interface).

Tsuchinoko's state machine sees the run complete, finalizes its data, and
transitions to Inactive. No new `adaptive.done` subject is needed.

## Testing

### Unit tests

**`tests/test_plan_ui_framework.py`**
- `plan_with_ui` decorator attaches `_plan_ui_class`
- `PlanState` signals work
- `get_plan_state()` / `set_plan_state()` roundtrip via ContextVar
- State is scoped per plan execution (doesn't leak between plans)

**`tests/test_nats_plan_bridge.py`**
- `subscribe()` creates queue and subscription
- `try_get()` returns queued message, or None if empty
- `publish()` forwards to IPCService
- `cleanup()` unsubscribes all
- Uses mock IPCService (no real broker)

**`tests/test_adaptive_plan.py`**
- Drive the plan generator manually (no RunEngine) with mock bridge and state
- Verify: plan yields `open_run` first, then polls, then measures, then `close_run`
- Verify: `exhaust_first=False` publishes `adaptive.measured` per measurement
- Verify: `exhaust_first=True` publishes once per batch
- Verify: `stop_requested` exits the loop
- Verify: timeout exits the loop

### Integration tests

**`tests/test_adaptive_plan_integration.py`**
- Requires real NATS broker (skip if not available)
- Mock LUCID (via IPCService) registered on a test prefix
- Mock Tsuchinoko on NATS publishes `tsuchinoko.targets`
- Run adaptive plan via real BlueskyEngine with mock ophyd motors/detectors
- Verify: measurements happen, `{prefix}.adaptive.measured` is received
- Verify: plan closes cleanly when stop flag is set

## File Structure

### New files
| File | Responsibility |
|------|----------------|
| `src/lucid/acquire/plan_ui.py` | `plan_with_ui` decorator, `PlanState`, `PlanUI`, ContextVar helpers |
| `src/lucid/acquire/nats_bridge.py` | `NATSPlanBridge` |
| `src/lucid/acquire/plans/adaptive.py` | `adaptive_experiment` plan + `AdaptivePlanState` |
| `src/lucid/acquire/plans/adaptive_ui.py` | `AdaptiveExperimentPanel` widget |
| `tests/acquire/test_plan_ui_framework.py` | Unit tests for plan UI framework |
| `tests/acquire/test_nats_plan_bridge.py` | Unit tests for NATS bridge |
| `tests/acquire/test_adaptive_plan.py` | Unit tests for adaptive plan |
| `tests/acquire/test_adaptive_plan_integration.py` | Integration tests (real NATS) |

### Modified files
| File | Changes |
|------|---------|
| `src/lucid/ui/panels/bluesky_panel.py` | Wrap plan selector in QTabWidget, add hooks for running plan UIs |
| `src/lucid/acquire/plans/registry.py` | Register `adaptive_experiment` plan |
| `src/lucid/acquire/engine/bluesky.py` | Inject plan state via ContextVar before running plan |

## Open Design Questions

1. **Where does the plan runner set the ContextVar?** It needs to happen
   *before* the plan generator is created (since `get_plan_state()` is
   called at the top of the plan). Likely in `BlueskyEngine` or the plan
   submission path.

2. **What if a plan aborts mid-execution?** The `finally: bridge.cleanup()`
   handles subscription cleanup. The UI tab is removed when the engine
   transitions to idle. Need to verify this edge case in tests.

3. **Can plan UI be reused across runs?** No — each plan execution creates
   a fresh state and UI. Prevents stale state leaking between runs.
