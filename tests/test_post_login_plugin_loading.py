"""Tests for the post-login plugin-loading sequence (core).

The background plugin wave (``PluginLoader.start_loading``) is deferred from
application startup to *after* the login screen, so only login-window plugins
(auth providers, theme) load before login. See
``docs/superpowers/specs/2026-06-20-post-login-plugin-loading-design.md``.

The startup/login/layout reorder as a whole cannot be meaningfully unit-tested
(it needs a real GUI + login run, validated on the box). These tests cover the
two testable seams:

1. the loader/arming sequence in ``lightfall.main``, and
2. the main-window layout latch that drives ``setup_default_layout`` +
   saved-state restoration + proactive init off the post-login
   ``loading_complete`` signal.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.auth.session import AuthState, Session, SessionManager, User


@pytest.fixture
def session_manager():
    """A pristine SessionManager singleton, reset around each test."""
    SessionManager.reset()
    sm = SessionManager.get_instance()
    yield sm
    SessionManager.reset()


def _fake_loader() -> MagicMock:
    """A stand-in PluginLoader that only records start_loading() calls."""
    loader = MagicMock()
    loader.start_loading = MagicMock()
    return loader


# --- _setup_plugins must NOT kick the background wave at startup -------------


def test_setup_plugins_does_not_start_loading(qapp, monkeypatch):
    """The plugin wave is deferred: _setup_plugins only loads preload plugins."""
    from lightfall import main as main_mod
    from lightfall.core.services import ServiceRegistry
    from lightfall.plugins.loader import PluginLoader

    started: list[int] = []
    # Record start_loading; no-op the (heavy) preload instantiation — its own
    # behavior is covered by the loader's dedicated tests.
    monkeypatch.setattr(PluginLoader, "start_loading", lambda self: started.append(1))
    monkeypatch.setattr(PluginLoader, "load_preload_plugins", lambda self: (0, 0))

    class _FakeApp:
        def __init__(self) -> None:
            self.services = ServiceRegistry()

    app = _FakeApp()
    main_mod._setup_plugins(app)

    assert started == [], "start_loading must not be called during startup"
    loader = app.services.get(PluginLoader)
    assert loader is not None
    # Non-preload plugins stay queued for the post-login wave.
    assert len(loader._load_queue) > 0


# --- arming the post-login wave ---------------------------------------------


def test_arming_does_not_start_loading_before_authentication(qapp, session_manager):
    from lightfall.main import _arm_post_login_plugin_load

    loader = _fake_loader()
    _arm_post_login_plugin_load(loader, session_manager)

    loader.start_loading.assert_not_called()


def test_authenticated_transition_starts_loading_once(qapp, session_manager):
    from lightfall.main import _arm_post_login_plugin_load

    loader = _fake_loader()
    _arm_post_login_plugin_load(loader, session_manager)

    session_manager._set_state(AuthState.AUTHENTICATED)
    assert loader.start_loading.call_count == 1

    # A later re-authentication (e.g. after session expiry) must not reload.
    session_manager._set_state(AuthState.UNAUTHENTICATED)
    session_manager._set_state(AuthState.AUTHENTICATED)
    assert loader.start_loading.call_count == 1


def test_already_authenticated_fires_immediately(qapp, session_manager):
    from lightfall.main import _arm_post_login_plugin_load

    # token=None -> attach_session skips the network mint round.
    session_manager.attach_session(Session(user=User(username="op")))
    assert session_manager.is_authenticated

    loader = _fake_loader()
    _arm_post_login_plugin_load(loader, session_manager)

    assert loader.start_loading.call_count == 1


def test_fire_safety_net_loads_for_guest_or_cancel(qapp, session_manager):
    """Guest / cancelled startup never reaches AUTHENTICATED; the returned
    fire() lets the caller load the wave once the login dialog closes."""
    from lightfall.main import _arm_post_login_plugin_load

    loader = _fake_loader()
    fire = _arm_post_login_plugin_load(loader, session_manager)

    fire()
    assert loader.start_loading.call_count == 1

    # A subsequent AUTHENTICATED (later in-app login) must not double-load.
    session_manager._set_state(AuthState.AUTHENTICATED)
    assert loader.start_loading.call_count == 1


# --- main-window layout latch -----------------------------------------------


def _patch_layout_recorders(window, monkeypatch, calls, *, default_layout_applied):
    """Replace the heavy layout methods with recorders.

    setup_default_layout normally computes _default_layout_applied from saved
    QSettings; here we force it so the restore branch is deterministic.
    """
    def _layout():
        calls.append("layout")
        window._default_layout_applied = default_layout_applied

    monkeypatch.setattr(window, "setup_default_layout", _layout)
    monkeypatch.setattr(window, "_restore_dock_state", lambda: calls.append("restore"))
    monkeypatch.setattr(
        window, "_maybe_start_proactive_init", lambda: calls.append("proactive")
    )

    class _Cfg:
        def get(self, key, default=None):
            return True

    window._config_manager = _Cfg()


def test_loading_complete_builds_layout_before_window_shown(qapp, monkeypatch):
    """Plugins finishing during the (hidden-window) login dialog build the
    layout immediately, but defer restore/proactive until the window shows."""
    from lightfall.ui import LFMainWindow

    window = LFMainWindow()
    calls: list[str] = []
    _patch_layout_recorders(window, monkeypatch, calls, default_layout_applied=False)

    window._on_plugin_loading_complete(3, 0)
    assert window._plugins_loaded is True
    assert calls == ["layout"], "layout built, but not finalized until shown"

    # Window appears -> finalize: panels are already registered, so restore the
    # saved layout on top of them, then start proactive init.
    window._window_shown = True
    window._finalize_layout_if_ready()
    assert calls == ["layout", "restore", "proactive"]


def test_finalize_restores_saved_layout_after_panels(qapp, monkeypatch):
    """Window shown first (waiting on plugins); when the wave completes the
    invariant 'panels registered before saved layout applied' holds."""
    from lightfall.ui import LFMainWindow

    window = LFMainWindow()
    calls: list[str] = []
    _patch_layout_recorders(window, monkeypatch, calls, default_layout_applied=False)

    window._window_shown = True
    window._on_plugin_loading_complete(2, 0)

    assert calls == ["layout", "restore", "proactive"]


def test_finalize_skips_restore_on_first_run(qapp, monkeypatch):
    """First run (no saved layout) applies the fresh default and skips restore."""
    from lightfall.ui import LFMainWindow

    window = LFMainWindow()
    calls: list[str] = []
    _patch_layout_recorders(window, monkeypatch, calls, default_layout_applied=True)

    window._window_shown = True
    window._on_plugin_loading_complete(0, 0)

    assert calls == ["layout", "proactive"]


def test_layout_built_once_across_repeated_completion(qapp, monkeypatch):
    from lightfall.ui import LFMainWindow

    window = LFMainWindow()
    calls: list[str] = []
    _patch_layout_recorders(window, monkeypatch, calls, default_layout_applied=True)
    window._window_shown = True

    window._on_plugin_loading_complete(1, 0)
    window._on_plugin_loading_complete(1, 0)

    assert calls.count("layout") == 1


def test_proactive_waits_for_window_shown(qapp, monkeypatch):
    from lightfall.ui import LFMainWindow

    window = LFMainWindow()
    calls: list[str] = []
    _patch_layout_recorders(window, monkeypatch, calls, default_layout_applied=True)

    # Plugins load while the window is still hidden -> no proactive init yet.
    window._on_plugin_loading_complete(1, 0)
    assert "proactive" not in calls

    window._window_shown = True
    window._finalize_layout_if_ready()
    assert "proactive" in calls


# --- early window-geometry restore (avoids default-size -> saved-size flash) --


def test_restore_window_geometry_applies_saved(qapp, monkeypatch):
    """Geometry is restored from saved settings (up-front, no panels needed)."""
    import PySide6.QtCore as QtCore

    from lightfall.ui import LFMainWindow

    window = LFMainWindow()
    saved = window.saveGeometry()  # real, valid QByteArray geometry

    class _FakeSettings:
        def value(self, key):
            return saved if key == "mainwindow/geometry" else None

    monkeypatch.setattr(QtCore, "QSettings", lambda *a, **k: _FakeSettings())
    restored = []
    monkeypatch.setattr(
        window, "restoreGeometry", lambda g: restored.append(g) or True
    )

    assert window._restore_window_geometry() is True
    assert restored == [saved]


def test_restore_window_geometry_noop_without_saved(qapp, monkeypatch):
    import PySide6.QtCore as QtCore

    from lightfall.ui import LFMainWindow

    window = LFMainWindow()

    class _FakeSettings:
        def value(self, key):
            return None

    monkeypatch.setattr(QtCore, "QSettings", lambda *a, **k: _FakeSettings())
    restored = []
    monkeypatch.setattr(window, "restoreGeometry", lambda g: restored.append(g))

    assert window._restore_window_geometry() is False
    assert restored == []
