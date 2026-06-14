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
