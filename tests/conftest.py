"""Pytest configuration and fixtures for Lightfall tests."""

import os

# Force ophyd onto its no-op "dummy" control layer before anything imports
# ophyd. Otherwise ophyd's import-time set_cl() falls through to caproto (no
# pyepics installed) and instantiates a global caproto Context, whose
# broadcaster/search/selector daemon threads run for the whole session. With
# no IOC present (as in CI) those threads — and any subscription callbacks —
# fire into Qt objects that qtbot has already deleted, segfaulting the xdist
# worker. The crashing test is arbitrary (whichever pumps the event loop at
# the time), which is why CI and local runs blame different tests. An explicit
# OPHYD_CONTROL_LAYER (e.g. for the gated integration suites) still wins.
os.environ.setdefault("OPHYD_CONTROL_LAYER", "dummy")

# Match production's global pyqtgraph image axis order. In the app this is set
# at startup by apply_pyqtgraph_theme(); pyqtgraph's built-in default is
# col-major, but Lightfall indexes images row-major ((row, col) = (y, x)) so
# ImageItems don't transpose the display. Set at import time, before any widget
# test constructs an ImageItem.
import pyqtgraph as _pg  # noqa: E402

_pg.setConfigOption("imageAxisOrder", "row-major")

import pytest  # noqa: E402

# pytest-qt provides the `qapp` and `qtbot` fixtures automatically.
# No custom qapp fixture needed - pytest-qt handles QApplication lifecycle
# including proper cleanup and CI/headless environment support.


@pytest.fixture(scope="session", autouse=True)
def _shutdown_thread_manager():
    """Cancel any QThreadFutures still running when the session ends.

    In production ThreadManager.shutdown() runs on QApplication.aboutToQuit,
    but pytest never finishes a Qt event loop, so that hookup never fires.
    Threads leaked by tests (e.g. BlueskyEngine queue processors) are then
    destroyed at interpreter exit while still running, which aborts the
    process ("QThread: Destroyed while thread is still running") and breaks
    the exit code even when every test passed.
    """
    yield
    from lightfall.utils.threads import get_thread_manager

    get_thread_manager().shutdown()


@pytest.fixture(autouse=True)
def _reset_toast_state():
    """Drain pyqttoast's process-global toast queue after every test.

    pyqttoast keeps ``Toast.__queue`` / ``Toast.__currently_shown`` as
    class-level lists. When a visible toast hides it schedules a one-shot
    ``QTimer`` that calls ``Toast.__show_next_in_queue()``. A test that queues
    a toast (more than ``maximumOnScreen`` at once) leaves it in that global
    queue; at teardown the toast's parent widget — and its C++ children — are
    destroyed, but the dead ``Toast`` object lingers in the queue. When any
    later test pumps the Qt event loop the stray timer fires, pops the dead
    toast and calls ``show()`` on a deleted ``QLabel``, raising ``RuntimeError``
    inside the event loop. pytest-qt's exception hook then fails whichever test
    happened to be pumping events — so the blamed test is arbitrary (e.g.
    ``test_waiting_hook``), the same failure shape as the ophyd dummy-CL issue
    documented above. Clearing the global queue between tests means a stray
    ``__show_next_in_queue`` finds an empty queue and is a harmless no-op.
    """
    yield
    try:
        from pyqttoast import Toast
    except Exception:
        return
    # reset() hides shown toasts and clears both lists, but it touches C++
    # widgets and can raise if a queued toast's parent was already destroyed
    # during this test's teardown — so never let it fail a passed test.
    try:
        Toast.reset()
    except Exception:
        pass
    # Guarantee the global lists end up empty even if reset() bailed early: a
    # single leftover (possibly dead) entry is all it takes to crash the next
    # test that pumps the event loop.
    for _attr in ("_Toast__queue", "_Toast__currently_shown"):
        try:
            getattr(Toast, _attr).clear()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _reset_theme_manager():
    """Drop the process-global ThemeManager between tests.

    StatusBarPlugin is deliberately *not* a QObject (see
    ``lightfall.plugins.statusbar_plugin._StatusBarSignals``), yet its
    subclasses do ``ThemeManager.get_instance().colors_changed.connect(
    self.update)`` in ``connect_signals()``. Qt only auto-disconnects a signal
    whose *receiver* is a QObject, so that connection outlives the plugin — and
    its qtbot-deleted ``_widget`` — for the rest of the session. A later test
    that emits ``colors_changed`` (any ``set_theme_by_name`` / ``set_font_size``,
    or ``tests/docking/test_manager_status.py``'s explicit ``colors_changed.emit()``)
    then calls ``update()`` on the dead widget and raises ``RuntimeError`` in the
    event loop, blamed on an arbitrary test — the same shape as the toast and
    ophyd issues above. Replacing the singleton each test drops every dangling
    connection along with the old instance. (The appearance tests already call
    ``ThemeManager.reset()`` in their own setup, so this is a no-op for them.)
    """
    yield
    try:
        from lightfall.ui.theme import ThemeManager

        ThemeManager.reset()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_qt_service_singletons():
    """Reset QObject-singleton services + theater manager after each test.

    These singletons own resources that outlive the test that created them:
    ``TiledService`` (a health-check ``QTimer``), ``ALSBeamStatusService`` (a
    poll ``QTimer``), ``DeviceConnectionManager`` (background connection
    workers), and the module-global ``theater_manager`` (proxy widgets + the
    ``partial`` slots wired to their ``expand_requested`` signals). Their
    ``reset``/``reset_instance`` methods stop the timer/worker and drop the
    instance, but today only the few test files that opt in call them — so
    under xdist a singleton created in one file can leak a live timer/worker
    into a later file on the same worker, whose deferred callback then fires
    into deleted Qt objects (the arbitrary-test crash class documented above).
    Resetting them here makes the guarantee process-wide.

    This is the *targeted* set the flakiness audit flagged as high/medium risk,
    not a blanket reset of every singleton: plain registries that deliberately
    seed state within a file (PanelRegistry, AgentRegistry, ...) are left to
    their own fixtures. Best-effort — cleanup must never fail a passed test.
    """
    yield
    try:
        from lightfall.services.tiled_service import TiledService

        TiledService.reset()
    except Exception:
        pass
    try:
        from lightfall.services.als_beam_status import ALSBeamStatusService

        ALSBeamStatusService.reset()
    except Exception:
        pass
    try:
        from lightfall.devices.connection_manager import DeviceConnectionManager

        DeviceConnectionManager.reset_instance()
    except Exception:
        pass
    try:
        from lightfall.devices.catalog import DeviceCatalog

        DeviceCatalog.reset_instance()
    except Exception:
        pass
    try:
        from lightfall.ui.theater.manager import theater_manager

        theater_manager._proxies.clear()
        theater_manager._slots.clear()
        theater_manager._overlay = None
    except Exception:
        pass
