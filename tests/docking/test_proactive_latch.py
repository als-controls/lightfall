"""Tests for the LFMainWindow proactive-init latch.

The latch methods only touch a handful of attributes, so they are
tested on a minimal stand-in object rather than a full LFMainWindow
(which drags in engine/session/services setup).
"""

from PySide6.QtCore import QObject, Signal

from lightfall.core.services import ServiceRegistry
from lightfall.plugins import PluginLoader
from lightfall.ui.mainwindow import LFMainWindow


class _FakeDocking:
    def __init__(self):
        self.started = 0

    def start_proactive_init(self):
        self.started += 1


class _Host:
    """Minimal stand-in carrying the latch attributes + real methods."""

    _watch_plugin_loading = LFMainWindow._watch_plugin_loading
    _on_plugin_loading_complete = LFMainWindow._on_plugin_loading_complete
    _maybe_start_proactive_init = LFMainWindow._maybe_start_proactive_init

    def __init__(self):
        self._window_shown = False
        self._plugins_loaded = False
        self._docking_manager = _FakeDocking()


class _FakeLoader(QObject):
    loading_complete = Signal(int, int)

    def __init__(self, loading: bool):
        super().__init__()
        self.is_loading = loading


class TestProactiveLatch:
    def setup_method(self):
        ServiceRegistry.reset()

    def teardown_method(self):
        ServiceRegistry.reset()

    def test_no_loader_starts_immediately(self):
        host = _Host()
        host._window_shown = True
        host._watch_plugin_loading()
        assert host._docking_manager.started == 1

    def test_loader_finished_starts_immediately(self):
        loader = _FakeLoader(loading=False)
        ServiceRegistry.get_instance().register_instance(PluginLoader, loader)
        host = _Host()
        host._window_shown = True
        host._watch_plugin_loading()
        assert host._docking_manager.started == 1

    def test_waits_for_loading_complete(self):
        loader = _FakeLoader(loading=True)
        ServiceRegistry.get_instance().register_instance(PluginLoader, loader)
        host = _Host()
        host._window_shown = True
        host._watch_plugin_loading()
        assert host._docking_manager.started == 0
        loader.loading_complete.emit(3, 0)
        assert host._docking_manager.started == 1

    def test_requires_window_shown(self):
        host = _Host()
        host._watch_plugin_loading()  # window not shown yet
        assert host._docking_manager.started == 0
