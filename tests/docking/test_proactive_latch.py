"""Tests for the LFMainWindow post-login layout + proactive-init latch.

The latch methods only touch a handful of attributes, so they are tested on a
minimal stand-in object rather than a full LFMainWindow (which drags in
engine/session/services setup).

Under post-login plugin loading, the background plugin wave runs after login;
its ``loading_complete`` drives ``_on_plugin_loading_complete``, which builds the
default layout once and then — only when the window is also shown — restores any
saved state and starts proactive panel init.
"""

from lightfall.ui.mainwindow import LFMainWindow


class _FakeDocking:
    def __init__(self):
        self.started = 0

    def start_proactive_init(self):
        self.started += 1


class _Host:
    """Minimal stand-in carrying the latch attributes + real methods."""

    _on_plugin_loading_complete = LFMainWindow._on_plugin_loading_complete
    _ensure_default_layout = LFMainWindow._ensure_default_layout
    _finalize_layout_if_ready = LFMainWindow._finalize_layout_if_ready
    _maybe_start_proactive_init = LFMainWindow._maybe_start_proactive_init

    def __init__(self):
        self._window_shown = False
        self._plugins_loaded = False
        self._default_layout_built = False
        self._default_layout_applied = True  # first-run: nothing to restore
        self._layout_finalized = False
        self._config_manager = None  # restore is skipped without a config
        self._docking_manager = _FakeDocking()
        self.layout_build_count = 0
        self.restore_count = 0

    # Heavy real methods stubbed out for the stand-in.
    def setup_default_layout(self):
        self.layout_build_count += 1

    def _restore_window_state(self):
        self.restore_count += 1


class TestPostLoginLatch:
    def test_starts_when_shown_then_loaded(self):
        host = _Host()
        host._window_shown = True
        host._on_plugin_loading_complete(3, 0)
        assert host.layout_build_count == 1
        assert host._docking_manager.started == 1

    def test_starts_when_loaded_then_shown(self):
        host = _Host()
        # Plugins finish while the window is still hidden (during login).
        host._on_plugin_loading_complete(3, 0)
        assert host.layout_build_count == 1  # layout built immediately
        assert host._docking_manager.started == 0  # but not finalized yet

        host._window_shown = True
        host._finalize_layout_if_ready()
        assert host._docking_manager.started == 1

    def test_requires_window_shown(self):
        host = _Host()
        host._on_plugin_loading_complete(1, 0)  # window not shown yet
        assert host._docking_manager.started == 0

    def test_requires_plugins_loaded(self):
        host = _Host()
        host._window_shown = True
        host._finalize_layout_if_ready()  # plugins not loaded yet
        assert host._docking_manager.started == 0

    def test_layout_and_finalize_run_once(self):
        host = _Host()
        host._window_shown = True
        host._on_plugin_loading_complete(1, 0)
        host._on_plugin_loading_complete(1, 0)
        assert host.layout_build_count == 1
        assert host._docking_manager.started == 1
