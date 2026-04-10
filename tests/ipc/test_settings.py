"""Tests for IPCSettingsPlugin."""

from __future__ import annotations

import pytest

from lucid.ui.preferences.ipc_settings import IPCSettingsPlugin


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
