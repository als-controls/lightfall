"""Simulated two-state actuators (shutters, gate valves).

Deliberately minimal: a state string that transitions through
opening/closing with a configurable travel delay. No physics.
"""

from __future__ import annotations

import threading

from ophyd import Component as Cpt
from ophyd import Device
from ophyd.signal import Signal
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
        self.state.put(initial)
        self._lock = threading.Lock()
        self._generation = 0
        self._pending_status = None
        self._pending_timer = None

    @property
    def is_open(self) -> bool:
        return self.state.get() == self.OPEN

    def set(self, value) -> DeviceStatus:
        target = (
            self.OPEN
            if str(value).lower() in ("open", "1", "true")
            else self.CLOSED
        )
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
