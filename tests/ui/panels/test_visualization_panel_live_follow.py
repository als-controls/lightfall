"""Tests for VisualizationPanel live-run follow + active-gated refresh."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QObject, Signal

from lightfall.ui.panels.visualization_panel import VisualizationPanel


class _StubEntry:
    """Minimal stand-in for a Tiled BlueskyRun entry."""

    def __init__(self, uid: str, stop=None):
        # Mirrors a Tiled BlueskyRun: metadata has "start" and "stop".
        self.metadata = {"start": {"uid": uid}, "stop": stop}

    def refresh(self):  # entries may expose refresh(); no-op here
        pass


class _FakeEngine(QObject):
    """Engine stub exposing the one signal the panel subscribes to."""

    sigOutput = Signal(str, dict)

    def subscribe(self, cb):
        return 0

    def unsubscribe(self, token):
        pass


def _install_fake_engine(monkeypatch) -> _FakeEngine:
    """Make get_engine() return a fake engine. Patch BEFORE constructing the panel."""
    engine = _FakeEngine()
    monkeypatch.setattr("lightfall.acquire.get_engine", lambda: engine)
    return engine


def _patch_tiled_service(monkeypatch, *, client, is_connected=True):
    """Mirror the existing helper in test_visualization_panel_actions.py."""
    service = MagicMock()
    service._client = client
    service.is_connected = is_connected
    monkeypatch.setattr(
        "lightfall.services.tiled_service.TiledService.get_instance",
        lambda: service,
    )
    return service


def test_initial_state(qtbot):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    assert panel._follow_live is True
    assert panel._live_run_uid is None
    assert panel._is_live is False
    assert panel._sync_retries == 0
    assert panel._follow_action is None


def test_shown_uid(qtbot):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    assert panel._shown_uid() is None
    panel._entry = _StubEntry("u-42")
    assert panel._shown_uid() == "u-42"


def test_open_run_from_user_disengages_follow(qtbot, monkeypatch):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    # Avoid the heavy widget machinery — only the follow flag matters here.
    monkeypatch.setattr(panel, "_pick_widget_class", lambda *a, **k: None)
    panel.open_run(_StubEntry("u1"), from_user=True)
    assert panel._follow_live is False


def test_open_run_auto_keeps_follow(qtbot, monkeypatch):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    monkeypatch.setattr(panel, "_pick_widget_class", lambda *a, **k: None)
    panel.open_run(_StubEntry("u1"), from_user=False)
    assert panel._follow_live is True


def test_sync_opens_when_active_follow_and_resolvable(qtbot, monkeypatch):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel.activate()  # is_active True
    panel._follow_live = True
    panel._live_run_uid = "u1"
    entry = _StubEntry("u1")
    monkeypatch.setattr(panel, "_resolve_entry", lambda uid: entry)
    opened = MagicMock()
    monkeypatch.setattr(panel, "open_run", opened)
    panel._sync_to_live_run()
    opened.assert_called_once_with(entry, from_user=False)


def test_sync_noop_when_inactive(qtbot, monkeypatch):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    # inactive by default (is_active False)
    panel._follow_live = True
    panel._live_run_uid = "u1"
    monkeypatch.setattr(panel, "_resolve_entry", lambda uid: _StubEntry("u1"))
    opened = MagicMock()
    monkeypatch.setattr(panel, "open_run", opened)
    panel._sync_to_live_run()
    opened.assert_not_called()


def test_sync_noop_when_follow_off(qtbot, monkeypatch):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel.activate()
    panel._follow_live = False
    panel._live_run_uid = "u1"
    monkeypatch.setattr(panel, "_resolve_entry", lambda uid: _StubEntry("u1"))
    opened = MagicMock()
    monkeypatch.setattr(panel, "open_run", opened)
    panel._sync_to_live_run()
    opened.assert_not_called()


def test_sync_noop_when_already_shown(qtbot, monkeypatch):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel.activate()
    panel._follow_live = True
    panel._live_run_uid = "u1"
    panel._entry = _StubEntry("u1")  # already showing u1
    opened = MagicMock()
    monkeypatch.setattr(panel, "open_run", opened)
    panel._sync_to_live_run()
    opened.assert_not_called()


def test_sync_schedules_retry_when_unresolvable(qtbot, monkeypatch):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel.activate()
    panel._follow_live = True
    panel._live_run_uid = "u1"
    monkeypatch.setattr(panel, "_resolve_entry", lambda uid: None)
    sched = MagicMock()
    monkeypatch.setattr(panel, "_schedule_sync_retry", sched)
    opened = MagicMock()
    monkeypatch.setattr(panel, "open_run", opened)
    panel._sync_to_live_run()
    opened.assert_not_called()
    sched.assert_called_once()


def test_timer_runs_when_live_and_active(qtbot):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel._is_live = True
    panel.activate()  # _on_activated -> _update_refresh
    assert panel._refresh_timer is not None
    assert panel._refresh_timer.isActive()


def test_deactivate_pauses_timer(qtbot):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel._is_live = True
    panel.activate()
    assert panel._refresh_timer is not None
    panel.deactivate()
    assert panel._refresh_timer is None  # _stop_refresh tears it down


def test_activate_does_catchup_refresh(qtbot):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    widget = MagicMock()
    panel._current_widget = widget
    panel._is_live = True
    panel.activate()
    widget.refresh.assert_called()  # immediate catch-up
    assert panel._refresh_timer is not None and panel._refresh_timer.isActive()


def test_no_timer_when_not_live(qtbot):
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel._is_live = False
    panel.activate()
    assert panel._refresh_timer is None


def test_start_doc_sets_live_uid_and_syncs(qtbot, monkeypatch):
    engine = _install_fake_engine(monkeypatch)
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    sync = MagicMock()
    monkeypatch.setattr(panel, "_sync_to_live_run", sync)
    engine.sigOutput.emit("start", {"uid": "u1"})
    assert panel._live_run_uid == "u1"
    sync.assert_called()


def test_descriptor_recovers_live_uid(qtbot, monkeypatch):
    engine = _install_fake_engine(monkeypatch)
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    sync = MagicMock()
    monkeypatch.setattr(panel, "_sync_to_live_run", sync)
    # No prior 'start' seen -> recover uid from descriptor.run_start.
    engine.sigOutput.emit("descriptor", {"run_start": "u9", "name": "primary"})
    assert panel._live_run_uid == "u9"
    sync.assert_called()


def test_stop_doc_clears_live_uid_and_stops(qtbot, monkeypatch):
    engine = _install_fake_engine(monkeypatch)
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    panel._live_run_uid = "u1"
    panel._is_live = True
    panel._current_widget = MagicMock()
    engine.sigOutput.emit("stop", {"run_start": "u1"})
    assert panel._live_run_uid is None
    assert panel._is_live is False


def test_start_while_inactive_defers_until_activate(qtbot, monkeypatch):
    engine = _install_fake_engine(monkeypatch)
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    entry = _StubEntry("u1")
    monkeypatch.setattr(panel, "_resolve_entry", lambda uid: entry)
    opened = MagicMock()
    monkeypatch.setattr(panel, "open_run", opened)
    # Inactive: start doc is cached but not opened.
    engine.sigOutput.emit("start", {"uid": "u1"})
    opened.assert_not_called()
    # Activate: now it opens.
    panel.activate()
    opened.assert_called_once_with(entry, from_user=False)
