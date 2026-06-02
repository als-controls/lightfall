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
