"""Tests for WaitingHookBridge."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication, QTimer

from lucid.acquire.engine.waiting_hook import WaitingHookBridge


@pytest.fixture
def qapp():
    """Ensure QApplication exists for Qt."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


@pytest.fixture
def bridge(qapp):
    """Create a WaitingHookBridge for testing."""
    return WaitingHookBridge()


def process_events_for(qapp, duration_ms: int = 250) -> None:
    """Process Qt events for the given duration."""
    deadline = time.monotonic() + duration_ms / 1000
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def make_watchable_status(name: str = "motor1") -> MagicMock:
    """Create a mock status object with .watch() support."""
    st = MagicMock()
    st.obj.name = name
    # watch and add_callback are real methods that store their callbacks
    st._watch_cbs: list = []
    st._done_cbs: list = []

    def _watch(cb):
        st._watch_cbs.append(cb)

    def _add_callback(cb):
        st._done_cbs.append(cb)

    st.watch = MagicMock(side_effect=_watch)
    st.add_callback = MagicMock(side_effect=_add_callback)
    return st


def make_simple_status(name: str = "det1") -> MagicMock:
    """Create a mock status object WITHOUT .watch() support."""
    st = MagicMock(spec=[])  # empty spec -> no attributes by default
    # Manually add only what we need
    st.obj = MagicMock()
    st.obj.name = name
    st._done_cbs: list = []

    def _add_callback(cb):
        st._done_cbs.append(cb)

    st.add_callback = _add_callback
    return st


class TestWaitingHookBridge:
    """Tests for the WaitingHookBridge class."""

    def test_watchable_status_emits_progress(self, bridge, qapp):
        """Watchable status emits sigDeviceProgress after timer flush.

        The bridge derives fraction-complete from (current, initial,
        target): for current=3 of a 0→10 move, |3-0|/|10-0| = 0.3.
        """
        received = []
        bridge.sigDeviceProgress.connect(lambda *args: received.append(args))

        st = make_watchable_status("motor1")
        bridge({st})

        assert len(st._watch_cbs) == 1
        st._watch_cbs[0](
            name="motor1", current=3.0, initial=0.0, target=10.0, fraction=0.7
        )

        # Process events to let the QTimer fire
        process_events_for(qapp, 250)

        assert len(received) >= 1
        name, current, initial, target, fraction = received[-1]
        assert name == "motor1"
        assert current == 3.0
        assert initial == 0.0
        assert target == 10.0
        assert fraction == pytest.approx(0.3)

    def test_fraction_at_move_endpoints(self, bridge, qapp):
        """Emitted fraction is 0.0 at start and 1.0 at end of move."""
        received = []
        bridge.sigDeviceProgress.connect(lambda *args: received.append(args))

        st = make_watchable_status("motor1")
        bridge({st})

        # Start of move: current == initial.
        st._watch_cbs[0](
            name="motor1", current=0.0, initial=0.0, target=10.0, fraction=1.0
        )
        process_events_for(qapp, 150)
        assert received[-1][4] == pytest.approx(0.0)

        # End of move: current == target.
        st._watch_cbs[0](
            name="motor1", current=10.0, initial=0.0, target=10.0, fraction=0.0
        )
        process_events_for(qapp, 150)
        assert received[-1][4] == pytest.approx(1.0)

    def test_fraction_none_emits_indeterminate(self, bridge, qapp):
        """ophyd sends fraction=None for zero-distance / NaN moves.

        The bridge must treat None as indeterminate (-1) rather than
        crashing on float(None).
        """
        received = []
        bridge.sigDeviceProgress.connect(lambda *args: received.append(args))

        st = make_watchable_status("motor_zero")
        bridge({st})

        # initial == target: ophyd computes fraction as None.
        st._watch_cbs[0](
            name="motor_zero", current=5.0, initial=5.0, target=5.0, fraction=None
        )

        process_events_for(qapp, 250)

        assert len(received) >= 1
        assert received[-1][4] == -1.0

    def test_fraction_never_decreases_during_backlash_overshoot(self, bridge, qapp):
        """Backlash compensation drives the motor past target and back.

        For a move 10 → 0 with EPICS backlash distance 1, the motor
        trajectory is 10 → 0 → -1 (overshoot) → 0 (final). ophyd's
        ``abs(target - current)/abs(initial - target)`` reports
        fraction-remaining ≈ 0.1 at the overshoot point, which the
        old inversion turned into 90% complete — so the bar dipped
        100% → 90% → 100% at the end of the move.

        The bridge must use distance-traveled semantics so the bar
        saturates at 100% during overshoot and never ticks down.
        """
        received_fractions: list[float] = []
        bridge.sigDeviceProgress.connect(
            lambda *args: received_fractions.append(args[4])
        )

        st = make_watchable_status("motor1")
        bridge({st})

        # Trajectory 10 → 0 → -1 → 0. ``fraction`` values are what
        # ophyd would emit (fraction-remaining); the bridge must NOT
        # rely on them and instead derive monotonic progress from
        # (current, initial, target).
        trajectory = [
            (10.0, 1.0),   # start
            (5.0, 0.5),    # halfway
            (0.0, 0.0),    # reached target
            (-1.0, 0.1),   # overshoot past target
            (0.0, 0.0),    # backlash return
        ]
        for current, ophyd_fraction in trajectory:
            st._watch_cbs[0](
                name="motor1",
                current=current,
                initial=10.0,
                target=0.0,
                fraction=ophyd_fraction,
            )
            process_events_for(qapp, 150)

        assert len(received_fractions) >= 4
        for i in range(1, len(received_fractions)):
            assert received_fractions[i] >= received_fractions[i - 1], (
                f"Bar ticked down at step {i}: "
                f"{received_fractions[i - 1]:.3f} -> "
                f"{received_fractions[i]:.3f} "
                f"(full sequence: {received_fractions})"
            )
        # End of move stays at 100%, even after overshoot.
        assert received_fractions[-1] == pytest.approx(1.0)

    def test_fraction_monotonic_for_negative_direction_move(self, bridge, qapp):
        """Negative-direction moves (start > target) tick up like positive ones.

        Move 10 → 0 with no overshoot: bar should go 0 → 50 → 100 %.
        This guards against any direction-asymmetric regression in the
        progress formula.
        """
        received_fractions: list[float] = []
        bridge.sigDeviceProgress.connect(
            lambda *args: received_fractions.append(args[4])
        )

        st = make_watchable_status("motor1")
        bridge({st})

        for current in (10.0, 5.0, 0.0):
            st._watch_cbs[0](
                name="motor1",
                current=current,
                initial=10.0,
                target=0.0,
                fraction=None,  # ignored by the new formula
            )
            process_events_for(qapp, 150)

        assert received_fractions == pytest.approx([0.0, 0.5, 1.0])

    def test_non_watchable_status_emits_indeterminate(self, bridge, qapp):
        """Non-watchable status emits fraction=-1 for indeterminate progress."""
        received = []
        bridge.sigDeviceProgress.connect(lambda *args: received.append(args))

        st = make_simple_status("det1")
        bridge({st})

        process_events_for(qapp, 250)

        assert len(received) >= 1
        name, current, initial, target, fraction = received[-1]
        assert name == "det1"
        assert fraction == -1.0

    def test_device_finished_on_completion(self, bridge, qapp):
        """sigDeviceFinished emits when a status completes."""
        finished = []
        bridge.sigDeviceFinished.connect(lambda name: finished.append(name))

        st = make_watchable_status("motor1")
        bridge({st})

        # Simulate completion
        assert len(st._done_cbs) == 1
        st._done_cbs[0]()

        process_events_for(qapp, 350)

        assert "motor1" in finished

    def test_wait_group_cleared(self, bridge, qapp):
        """Calling with None emits sigWaitGroupCleared."""
        cleared = []
        bridge.sigWaitGroupCleared.connect(lambda: cleared.append(True))

        # First add some statuses
        st = make_watchable_status("motor1")
        bridge({st})

        # Then clear
        bridge(None)
        process_events_for(qapp, 350)

        assert len(cleared) == 1

    def test_timer_stops_after_flush_on_none(self, bridge, qapp):
        """Timer stops after flush processes the group-cleared flag."""
        st = make_watchable_status("motor1")
        bridge({st})

        # Let the timer start (queued connection, needs event processing)
        process_events_for(qapp, 50)
        assert bridge._timer.isActive()

        bridge(None)

        # Timer stop is deferred to the next _flush on the main thread
        process_events_for(qapp, 250)
        assert not bridge._timer.isActive()

    def test_coalesces_rapid_updates(self, bridge, qapp):
        """Multiple watch updates before a flush tick are coalesced."""
        received = []
        bridge.sigDeviceProgress.connect(lambda *args: received.append(args))

        st = make_watchable_status("motor1")
        bridge({st})

        # Fire multiple watch updates before the timer can flush, as
        # the move progresses from current=0 to current=9.
        for i in range(10):
            st._watch_cbs[0](
                name="motor1",
                current=float(i),
                initial=0.0,
                target=9.0,
                fraction=1.0 - i / 9.0,
            )

        # Only the last value should survive coalescing
        process_events_for(qapp, 250)

        # We should have gotten at most a couple of emissions (not 10)
        # and the final one should have the last value: current=9.0,
        # fraction (complete) ≈ 1.0.
        assert len(received) >= 1
        last = received[-1]
        assert last[1] == 9.0  # current
        assert abs(last[4] - 1.0) < 0.01  # fraction complete

    def test_multiple_statuses(self, bridge, qapp):
        """Multiple status objects are tracked independently."""
        received = {}
        bridge.sigDeviceProgress.connect(
            lambda name, cur, ini, tgt, frac: received.update({name: (cur, ini, tgt, frac)})
        )

        st1 = make_watchable_status("motor1")
        st2 = make_watchable_status("motor2")
        bridge({st1, st2})

        st1._watch_cbs[0](name="motor1", current=1.0, initial=0.0, target=5.0, fraction=0.8)
        st2._watch_cbs[0](name="motor2", current=3.0, initial=0.0, target=10.0, fraction=0.7)

        process_events_for(qapp, 250)

        assert "motor1" in received
        assert "motor2" in received
        assert received["motor1"][0] == 1.0
        assert received["motor2"][0] == 3.0

    def test_duplicate_status_not_resubscribed(self, bridge, qapp):
        """Calling with the same status object twice doesn't double-subscribe."""
        st = make_watchable_status("motor1")
        bridge({st})
        bridge({st})

        # watch() should only have been called once
        assert st.watch.call_count == 1

    def test_status_name_fallback(self, bridge, qapp):
        """Status name extraction falls back gracefully."""
        # Status with no obj.name — falls back to .name
        st = MagicMock(spec=[])
        st.name = "fallback_name"
        st._done_cbs = []
        st.add_callback = lambda cb: st._done_cbs.append(cb)

        finished = []
        bridge.sigDeviceFinished.connect(lambda name: finished.append(name))

        bridge({st})

        # Trigger completion
        st._done_cbs[0]()
        process_events_for(qapp, 350)

        assert "fallback_name" in finished

    def test_none_without_prior_statuses(self, bridge, qapp):
        """Calling with None when no statuses are active doesn't error."""
        cleared = []
        bridge.sigWaitGroupCleared.connect(lambda: cleared.append(True))

        bridge(None)
        process_events_for(qapp, 350)

        assert len(cleared) == 1

    def test_cross_thread_done_callback(self, bridge, qapp):
        """Done callback fired from a background thread delivers via buffer."""
        import threading

        finished = []
        bridge.sigDeviceFinished.connect(lambda name: finished.append(name))

        st = make_watchable_status("motor_bg")
        bridge({st})

        # Process events so the timer starts
        process_events_for(qapp, 50)

        # Fire the done callback from a real background thread
        assert len(st._done_cbs) == 1
        bg = threading.Thread(target=st._done_cbs[0])
        bg.start()
        bg.join(timeout=2.0)

        # Let the flush timer pick up the buffered done event
        process_events_for(qapp, 250)

        assert "motor_bg" in finished

    def test_cross_thread_watch_callback(self, bridge, qapp):
        """Watch callback fired from a background thread is buffered safely."""
        import threading

        received = []
        bridge.sigDeviceProgress.connect(lambda *args: received.append(args))

        st = make_watchable_status("motor_bg2")
        bridge({st})

        process_events_for(qapp, 50)

        # Fire watch callback from a background thread
        def fire_watch():
            st._watch_cbs[0](
                name="motor_bg2", current=5.0, initial=0.0, target=10.0, fraction=0.5
            )

        bg = threading.Thread(target=fire_watch)
        bg.start()
        bg.join(timeout=2.0)

        process_events_for(qapp, 250)

        assert len(received) >= 1
        assert received[-1][0] == "motor_bg2"
        assert received[-1][1] == 5.0
