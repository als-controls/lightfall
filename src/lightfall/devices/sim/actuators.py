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
        if self.state.get() == target:
            status.set_finished()
            return status

        self.state.put(self.OPENING if target == self.OPEN else self.CLOSING)

        def _arrive() -> None:
            self.state.put(target)
            status.set_finished()

        timer = threading.Timer(self._travel_time, _arrive)
        timer.daemon = True
        timer.start()
        return status
