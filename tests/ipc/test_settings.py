"""Tests for IPCSettingsPlugin (the form itself is covered in tests/ui/preferences/test_ipc_form.py)."""

from __future__ import annotations

from lightfall.ui.preferences.ipc_settings import IPCSettingsPlugin


class TestIPCSettingsPlugin:
    def test_name(self):
        plugin = IPCSettingsPlugin()
        assert plugin.name == "ipc"

    def test_display_name(self):
        plugin = IPCSettingsPlugin()
        assert plugin.display_name == "IPC"

    def test_is_a_form_page(self):
        assert IPCSettingsPlugin().is_form_page()
