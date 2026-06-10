"""Pytest configuration and fixtures for Lightfall tests."""

import pytest

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
