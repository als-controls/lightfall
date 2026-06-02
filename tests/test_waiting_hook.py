"""Tests for WaitingHookBridge."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication, QTimer

from lightfall.acquire.engine.waiting_hook import WaitingHookBridge


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

        ophyd's MoveStatus reports fraction *remaining*; the bridge must
        invert it so consumers see fraction *complete*. Feeding 0.7
        remaining (i.e. 30% of the move done) must emit 0.3 complete.
        """
        received = []
        bridge.sigDeviceProgress.connect(lambda *args: received.append(args))

        st = make_watchable_status("motor1")
        bridge({st})

        # Simulate a watch callback from the RE thread with ophyd-style
        # fraction-remaining of 0.7 (move is 30% complete).
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

    def test_fraction_inverted_at_move_endpoints(self, bridge, qapp):
        """Bridge inverts ophyd's fraction-remaining at both endpoints.

        ophyd reports fraction=1.0 at move start and fraction=0.0 at move
        end; the bridge must emit 0.0 and 1.0 respectively.
        """
        received = []
        bridge.sigDeviceProgress.connect(lambda *args: received.append(args))

        st = make_watchable_status("motor1")
        bridge({st})

        # Start of move: ophyd fraction-remaining = 1.0.
        st._watch_cbs[0](
            name="motor1", current=0.0, initial=0.0, target=10.0, fraction=1.0
        )
        process_events_for(qapp, 150)
        assert received[-1][4] == pytest.approx(0.0)

        # End of move: ophyd fraction-remaining = 0.0.
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

        # Fire multiple watch updates before timer can flush. Feed
        # ophyd-style fraction-remaining ramping 1.0 → 0.0 as the move
        # progresses from current=0 to current=9.
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
        # bridge-emitted fraction (complete) ≈ 1.0.
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

        # ophyd-style fraction-remaining values; bridge inverts on emit.
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
