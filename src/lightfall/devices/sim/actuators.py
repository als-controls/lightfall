"""Simulated two-state actuators (shutters, gate valves).

Deliberately minimal: a state string that transitions through
opening/closing with a configurable travel delay. No physics.
"""

from __future__ import annotations

import threading
import time

from loguru import logger
from ophyd import Component as Cpt
from ophyd import Device
from ophyd.signal import Signal
from ophyd.sim import SynSignal
from ophyd.status import DeviceStatus


class SimShutter(Device):
    """Simulated shutter or gate valve.

    ``set("open")`` / ``set("close")`` return a status that completes
    after ``travel_time`` seconds, passing through the transitional
    opening/closing state so UIs can show motion.
    """

    OPEN = "open"
    CLOSED = "closed"
    OPENING = "opening"
    CLOSING = "closing"

    state = Cpt(Signal, value="closed", kind="hinted")

    def __init__(
        self,
        *args,
        travel_time: float = 0.2,
        initial: str = "closed",
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._travel_time = travel_time
        if initial != self.CLOSED:
            self.state.put(initial)
        self._lock = threading.RLock()
        self._generation = 0
        self._pending_status = None
        self._pending_timer = None

    @property
    def is_open(self) -> bool:
        return self.state.get() == self.OPEN

    def set(self, value) -> DeviceStatus:
        normalized = str(value).lower()
        if normalized in ("open", "1", "true"):
            target = self.OPEN
        else:
            if normalized not in ("close", "closed", "0", "false"):
                logger.warning(
                    "SimShutter.set: unrecognized value {!r}, "
                    "coercing to CLOSED (fail-safe)",
                    value,
                )
            target = self.CLOSED
        status = DeviceStatus(self)

        with self._lock:
            self._generation += 1
            gen = self._generation

            if self.state.get() == target:
                status.set_finished()
                return status

            # Cancel any pending timer and complete any pending status
            if self._pending_timer is not None:
                self._pending_timer.cancel()
                self._pending_timer = None

            if self._pending_status is not None and not self._pending_status.done:
                self._pending_status.set_finished()

            # Store new status and prepare timer
            self._pending_status = status
            self.state.put(self.OPENING if target == self.OPEN else self.CLOSING)

            def _arrive() -> None:
                with self._lock:
                    if gen != self._generation:
                        return
                    self.state.put(target)
                    status.set_finished()
                    self._pending_status = None

            timer = threading.Timer(self._travel_time, _arrive)
            timer.daemon = True
            timer.start()
            self._pending_timer = timer

        return status


class SimTemperatureController(Device):
    """Simulated temperature controller: readback slews toward setpoint.

    The readback recomputes on ``trigger()`` (SynSignal semantics, same
    as the other mock sensors) — a ``count()`` triggers then reads.
    No PID, no overshoot: linear approach at ``rate`` kelvin/second.

    ``clock`` must be a monotonically non-decreasing callable (e.g. the
    default ``time.monotonic``); the slew computation assumes elapsed
    time between calls is never negative.
    """

    setpoint = Cpt(Signal, value=295.0, kind="normal")
    readback = Cpt(SynSignal, kind="hinted")

    def __init__(
        self,
        *args,
        rate: float = 2.0,
        initial: float = 295.0,
        clock=time.monotonic,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        if rate <= 0:
            raise ValueError(f"rate must be > 0, got {rate!r}")
        self._rate = rate
        self._clock = clock
        self._anchor_value = float(initial)
        self._anchor_time = clock()
        self._previous_target = float(initial)
        self.setpoint.put(float(initial))
        self.setpoint.subscribe(self._on_setpoint_changed, run=False)
        self.readback.sim_set_func(self._compute)
        self.readback.trigger()

    def _on_setpoint_changed(self, *args, **kwargs) -> None:
        # Re-anchor the slew at the current (computed) temperature,
        # using the OLD target to compute current position.
        self._anchor_value = self._compute_toward(self._previous_target)
        self._previous_target = float(self.setpoint.get())
        self._anchor_time = self._clock()

    def _compute_toward(self, target: float) -> float:
        """Compute position moving toward a specific target."""
        elapsed = self._clock() - self._anchor_time
        delta = target - self._anchor_value
        step = self._rate * elapsed
        if abs(delta) <= step:
            return target
        return self._anchor_value + step * (1.0 if delta > 0 else -1.0)

    def _compute(self) -> float:
        return self._compute_toward(float(self.setpoint.get()))
