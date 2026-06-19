"""Tests for IPCSettingsPlugin."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.ui.preferences.ipc_settings import IPCSettingsPlugin


class TestIPCSettingsPlugin:
    def test_name(self):
        plugin = IPCSettingsPlugin()
        assert plugin.name == "ipc"

    def test_display_name(self):
        plugin = IPCSettingsPlugin()
        assert plugin.display_name == "IPC"

    def test_validate_empty_url_is_valid(self, qapp):
        plugin = IPCSettingsPlugin()
        plugin.create_widget()
        plugin._url_edit.setText("")
        assert plugin.validate() == []

    def test_validate_valid_url(self, qapp):
        plugin = IPCSettingsPlugin()
        plugin.create_widget()
        plugin._url_edit.setText("nats://localhost:4222")
        assert plugin.validate() == []

    def test_validate_bad_scheme(self, qapp):
        plugin = IPCSettingsPlugin()
        plugin.create_widget()
        plugin._url_edit.setText("http://localhost:4222")
        errors = plugin.validate()
        assert len(errors) > 0


class TestDisplayNameField:
    def test_display_name_field_exists(self, qapp):
        plugin = IPCSettingsPlugin()
        widget = plugin.create_widget()
        assert plugin._display_name_edit is not None

    def test_load_saves_display_name(self, qapp, monkeypatch):
        plugin = IPCSettingsPlugin()
        plugin.create_widget()
        mock_prefs = MagicMock()
        mock_prefs.get = MagicMock(side_effect=lambda k, d="": {
            "ipc_nats_url": "",
            "ipc_topic_prefix": "als.7011",
            "ipc_display_name": "CMS Hutch",
        }.get(k, d))
        monkeypatch.setattr(
            "lightfall.ui.preferences.ipc_settings.PreferencesManager.get_instance",
            lambda: mock_prefs,
        )
        plugin.load_settings()
        assert plugin._display_name_edit.text() == "CMS Hutch"

    def test_save_persists_display_name(self, qapp, monkeypatch):
        plugin = IPCSettingsPlugin()
        plugin.create_widget()
        plugin._display_name_edit.setText("My Hutch")
        mock_prefs = MagicMock()
        monkeypatch.setattr(
            "lightfall.ui.preferences.ipc_settings.PreferencesManager.get_instance",
            lambda: mock_prefs,
        )
        plugin.save_settings()
        calls = {c[0][0]: c[0][1] for c in mock_prefs.set.call_args_list}
        assert calls["ipc_display_name"] == "My Hutch"


class TestLocalNatsServerGroup:
    def test_fields_exist(self, qapp):
        plugin = IPCSettingsPlugin()
        plugin.create_widget()
        assert plugin._local_enable_cb is not None
        assert plugin._local_port_edit is not None
        assert plugin._local_status_label is not None

    def test_enabling_greys_url_field(self, qapp):
        plugin = IPCSettingsPlugin()
        plugin.create_widget()
        plugin._local_enable_cb.setChecked(True)
        assert not plugin._url_edit.isEnabled()
        plugin._local_enable_cb.setChecked(False)
        assert plugin._url_edit.isEnabled()

    def test_validate_rejects_bad_port(self, qapp):
        plugin = IPCSettingsPlugin()
        plugin.create_widget()
        plugin._local_enable_cb.setChecked(True)
        plugin._local_port_edit.setText("999999")
        assert any("port" in e.lower() for e in plugin.validate())

    def test_load_and_save_roundtrip(self, qapp, monkeypatch):
        from lightfall.ui.preferences import ipc_settings as mod

        store = {}
        mock_prefs = MagicMock()
        mock_prefs.get = MagicMock(side_effect=lambda k, d=None: {
            "ipc_nats_url": "nats://site:4222",
            "ipc_topic_prefix": "als.7011",
            "ipc_display_name": "",
            "ipc_use_local_nats": True,
            "ipc_local_nats_port": 4299,
        }.get(k, d))
        mock_prefs.set = MagicMock(side_effect=lambda k, v: store.__setitem__(k, v))
        monkeypatch.setattr(mod.PreferencesManager, "get_instance", lambda: mock_prefs)

        plugin = IPCSettingsPlugin()
        plugin.create_widget()
        plugin.load_settings()
        assert plugin._local_enable_cb.isChecked() is True
        assert plugin._local_port_edit.text() == "4299"
        assert not plugin._url_edit.isEnabled()

        plugin.save_settings()
        assert store["ipc_use_local_nats"] is True
        assert store["ipc_local_nats_port"] == 4299
