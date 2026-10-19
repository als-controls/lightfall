"""Regressions for the phase-4c final review (I-1 to I-6)."""
# ruff: noqa: F811  (fixtures imported from sibling test modules)
from __future__ import annotations

import pytest

from lightfall.scopes.cache import SnapshotCache
from lightfall.scopes.identity import derived_default
from lightfall.scopes.journal import WriteJournal
from lightfall.ui.preferences.manager import PreferencesManager
from tests.ui.preferences.test_manager_identity import FakeCM
from tests.ui.preferences.test_scoped_backend import Deferred, FakeLocal, Server, sync_run
from tests.ui.preferences.test_scoped_live import CountingServer, FakeIPC


@pytest.fixture
def bound(qapp, monkeypatch, tmp_path):
    """Manager on a branch:7.0.1.1 console with local scope files and a fake server."""
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    files = tmp_path / "scopes"
    files.mkdir()
    (files / "facility.yaml").write_text("settings:\n  tiled_url: https://facility\n",
                                         encoding="utf-8")
    (files / "branch-7.0.1.1.yaml").write_text(
        "settings:\n  device_bcs_port: 5577\nlocks:\n  device_bcs_port: site standard\n",
        encoding="utf-8")
    import lightfall.ui.preferences.scoped_backend as sb

    server = Server()

    def fake_build(local, *, identity, bind, user_preferences):
        return sb.ScopedBackend(local, client=server, cache=SnapshotCache(tmp_path / "c"),
                                journal=WriteJournal(tmp_path / "j.json"), bind=bind,
                                identity=identity, user_preferences=user_preferences,
                                run=sync_run, notify=lambda *a: None, poll_ms=0,
                                ipc=lambda: None)

    monkeypatch.setattr(sb, "build_scoped_backend", fake_build)
    PreferencesManager.reset()
    manager = PreferencesManager.get_instance()
    manager.set_config_manager(FakeCM("branch:7.0.1.1", device_bcs_port=1))
    manager.load_local_scopes(files)
    yield manager, server
    manager.deactivate_scopes()
    PreferencesManager.reset()


# I-1 -----------------------------------------------------------------------

def test_offline_login_keeps_local_file_values_and_locks(bound):
    manager, server = bound
    server.down = True
    manager.activate_scopes("u1", "beamline:7.0.1")
    assert manager.get("device_bcs_port") == 5577
    assert manager.get("tiled_url") == "https://facility"


# I-2 -----------------------------------------------------------------------

def test_logout_emits_only_real_changes_once(bound):
    manager, server = bound
    server.rows[("beamline:7.0.1", "tiled_url")] = ("https://beamline", 1)
    manager.activate_scopes("u1", "beamline:7.0.1")
    assert manager.get("tiled_url") == "https://beamline"
    port, url = [], []
    manager.subscribe("device_bcs_port", port.append)
    manager.subscribe("tiled_url", url.append)
    manager.deactivate_scopes()
    assert port == []                       # 5577 before and after: no flicker
    assert url == ["https://facility"]      # one emission, the final value


# I-3 -----------------------------------------------------------------------

def test_event_during_inflight_refresh_is_not_lost(qapp, tmp_path):
    from lightfall.ui.preferences.scoped_backend import ScopedBackend

    run, server, ipc = Deferred(), CountingServer(), FakeIPC()
    backend = ScopedBackend(FakeLocal(), client=server, cache=SnapshotCache(tmp_path / "c"),
                            journal=WriteJournal(tmp_path / "j.json"), bind="beamline:7.0.1",
                            identity="u1", run=run, notify=lambda *a: None, poll_ms=0,
                            ipc=lambda: ipc, debounce_ms=0)
    backend.activate()
    run.run_all()
    before = server.chains
    backend.refresh()                                     # poll in flight
    ipc.send({"scope": "beamline:7.0.1", "key": "tiled_url", "version": 1})
    run.run_all()
    assert server.chains == before + 2                    # the event's refresh ran too


# I-4 -----------------------------------------------------------------------

def test_saving_the_derived_value_stores_nothing(bound):
    manager, _ = bound
    manager.set_unless_derived("ipc_topic_prefix", "als.7011")
    assert manager.explicit("ipc_topic_prefix") is None
    assert manager.get("ipc_topic_prefix", "x") == "als.7011"
    manager.set_unless_derived("ipc_topic_prefix", "")
    assert manager.explicit("ipc_topic_prefix") is None
    manager.set_unless_derived("ipc_topic_prefix", "dev.me")
    assert manager.explicit("ipc_topic_prefix") == "dev.me"


def test_ipc_page_shows_derived_prefix_as_placeholder(bound, qtbot):
    from lightfall.ui.preferences.ipc_settings import IPCSettingsPlugin

    manager, _ = bound
    plugin = IPCSettingsPlugin()
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    plugin.load_settings()
    assert plugin._prefix_edit.text() == ""
    assert plugin._prefix_edit.placeholderText() == "als.7011"
    plugin.save_settings()
    assert manager.explicit("ipc_topic_prefix") is None


# I-5 / I-6 -----------------------------------------------------------------

def test_tiled_beamline_is_the_bind_name_and_synoptic_key_is_not_derived():
    assert derived_default("tiled_beamline", "branch:7.0.1.1", "als") == "7.0.1.1"
    assert derived_default("tiled_beamline", "beamline:4.0.2", "als") == "4.0.2"
    assert derived_default("current_beamline", "branch:7.0.1.1", "als") is None
