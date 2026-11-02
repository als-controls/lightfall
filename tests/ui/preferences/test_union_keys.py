"""Item-merged ("union") keys in the scope API: plugins.enabled is the reference."""
# ruff: noqa: F811  (the make fixture is imported from test_scoped_backend)
from __future__ import annotations

import pytest

from lightfall.ui.preferences.manager import PreferencesManager
from tests.ui.preferences.test_manager_identity import FakeCM
from tests.ui.preferences.test_scoped_backend import (
    FakeLocal,
    Server,
    make,  # noqa: F401, F811 - fixture
)

KEY = "plugins.enabled"


def test_item_origins_for_plugins_enabled(make):
    server = Server()
    server.rows[("beamline:7.0.1", KEY)] = ({"a": False}, 1)
    server.rows[("user:u1", KEY)] = ({"b": True}, 1)
    backend, *_ = make(server=server)
    backend.activate()
    assert backend.item_origins(KEY) == {"a": "beamline:7.0.1", "b": "user:u1"}


def test_resolved_items_merges_by_item(make):
    server = Server()
    server.rows[("beamline:7.0.1", KEY)] = ({"a": False, "b": False}, 1)
    server.rows[("user:u1", KEY)] = ({"b": True}, 1)
    backend, *_ = make(server=server)
    backend.activate()
    assert backend.get(KEY) == {"a": False, "b": True}
    assert backend.item_origins(KEY) == {"a": "beamline:7.0.1", "b": "user:u1"}


def test_scoped_manager_resolved_items(make):
    server = Server()
    server.rows[("beamline:7.0.1", KEY)] = ({"a": False}, 1)
    server.rows[("user:u1", KEY)] = ({"b": True}, 1)
    backend, *_ = make(server=server)
    backend.activate()
    pm = PreferencesManager.__new__(PreferencesManager)
    pm._scoped = backend
    assert pm.resolved_items(KEY) == {"a": False, "b": True}
    assert pm.item_origins(KEY) == {"a": "beamline:7.0.1", "b": "user:u1"}


def test_scalar_key_has_no_item_origins(make):
    server = Server()
    server.rows[("beamline:7.0.1", "tiled_url")] = ("https://bl", 1)
    backend, *_ = make(server=server)
    backend.activate()
    assert backend.item_origins("tiled_url") == {}


def test_plugins_enabled_has_a_console_layer(make):
    server = Server()
    server.rows[("beamline:7.0.1", KEY)] = ({"c": False, "d": False}, 1)
    server.rows[("user:u1", KEY)] = ({"b": True, "d": True}, 1)
    backend, *_ = make(server=server, local=FakeLocal(**{KEY: {"c": True}}))
    backend.activate()
    # plugins.enabled is a LOCAL_LAYER_KEY: this console's items sit between branch and user.
    assert backend.get(KEY) == {"b": True, "c": True, "d": True}
    assert backend.item_origins(KEY) == {"b": "user:u1", "c": "local", "d": "user:u1"}


def test_union_key_without_console_layer_ignores_the_yaml(make):
    server = Server()
    server.rows[("user:u1", "monitors.enabled")] = ({"b": True}, 1)
    backend, *_ = make(server=server, local=FakeLocal(**{"monitors.enabled": {"c": True}}))
    backend.activate()
    assert backend.get("monitors.enabled") == {"b": True}


@pytest.fixture
def pm(qapp, monkeypatch):
    monkeypatch.delenv("LIGHTFALL_SCOPES_DISABLED", raising=False)
    PreferencesManager.reset()
    yield PreferencesManager.get_instance()
    PreferencesManager.reset()


def test_off_mode_every_item_is_local(pm):
    pm.set_config_manager(FakeCM(None, **{KEY: {"a": False, "b": True}}))
    assert pm.get(KEY) == {"a": False, "b": True}
    assert pm.resolved_items(KEY) == {"a": False, "b": True}
    assert pm.item_origins(KEY) == {"a": "local", "b": "local"}


def test_off_mode_nothing_set_is_empty(pm):
    pm.set_config_manager(FakeCM(None))
    assert pm.resolved_items(KEY) == {}
    assert pm.item_origins(KEY) == {}


def test_resolved_items_ignores_non_dict(pm):
    pm.set_config_manager(FakeCM(None, **{KEY: ["x"]}))
    assert pm.resolved_items(KEY) == {}


def _files(tmp_path):
    (tmp_path / "facility.yaml").write_text("settings:\n  tiled_url: https://f\n", encoding="utf-8")
    (tmp_path / "beamline-7.0.1.yaml").write_text(
        "settings:\n  plugins.enabled:\n    a: false\n    b: false\n", encoding="utf-8")


def test_local_scope_files_merge_items(pm, tmp_path):
    _files(tmp_path)
    pm.set_config_manager(FakeCM("beamline:7.0.1", **{KEY: {"b": True, "c": True}}))
    assert pm.load_local_scopes(tmp_path) is True
    assert pm.get(KEY) == {"a": False, "b": True, "c": True}
    assert pm.resolved_items(KEY) == {"a": False, "b": True, "c": True}
    assert pm.item_origins(KEY) == {"a": "beamline:7.0.1", "b": "local", "c": "local"}


def test_local_scope_files_without_console_items(pm, tmp_path):
    _files(tmp_path)
    pm.set_config_manager(FakeCM("beamline:7.0.1"))
    assert pm.load_local_scopes(tmp_path) is True
    assert pm.get(KEY) == {"a": False, "b": False}
    assert pm.item_origins(KEY) == {"a": "beamline:7.0.1", "b": "beamline:7.0.1"}


def test_console_empty_map_keeps_file_items(pm, tmp_path):
    _files(tmp_path)
    for console in ({}, {"z": None}):
        pm.set_config_manager(FakeCM("beamline:7.0.1", **{KEY: console}))
        assert pm.load_local_scopes(tmp_path) is True
        assert pm.get(KEY) == {"a": False, "b": False}
        assert pm.item_origins(KEY) == {"a": "beamline:7.0.1", "b": "beamline:7.0.1"}


def test_console_only_union_key_is_local(pm, tmp_path):
    (tmp_path / "facility.yaml").write_text("settings:\n  tiled_url: https://f\n", encoding="utf-8")
    (tmp_path / "beamline-7.0.1.yaml").write_text("settings:\n  theme: dark\n", encoding="utf-8")
    pm.set_config_manager(FakeCM("beamline:7.0.1", **{KEY: {"t": True}}))
    assert pm.load_local_scopes(tmp_path) is True
    backend = pm._local_scopes
    assert not backend.owns(KEY)  # no scope file sets it
    assert backend.get(KEY) == {"t": True}
    assert backend.item_origins(KEY) == {"t": "local"}
    assert pm.get(KEY) == {"t": True}
    assert pm.item_origins(KEY) == {"t": "local"}


def test_lock_suppresses_console_items(pm, tmp_path):
    _files(tmp_path)
    (tmp_path / "beamline-7.0.1.yaml").write_text(
        "settings:\n  plugins.enabled:\n    a: false\nlocks:\n  plugins.enabled: site\n",
        encoding="utf-8")
    pm.set_config_manager(FakeCM("beamline:7.0.1", **{KEY: {"c": True}}))
    assert pm.load_local_scopes(tmp_path) is True
    assert pm.get(KEY) == {"a": False}
    assert pm.item_origins(KEY) == {"a": "beamline:7.0.1"}


def test_none_item_drops_broader_items_like_the_resolver(make):
    # Same semantics as scope_api.inherited_items: None removes what broader layers gave.
    server = Server()
    server.rows[("beamline:7.0.1", KEY)] = ({"a": False, "b": False, "c": False}, 1)
    server.rows[("user:u1", KEY)] = ({"b": None}, 1)
    backend, *_ = make(server=server, local=FakeLocal(**{KEY: {"a": None}}))
    backend.activate()
    assert backend.get(KEY) == {"c": False}
    assert backend.item_origins(KEY) == {"c": "beamline:7.0.1"}
