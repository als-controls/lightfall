"""Proxy page: the keys it writes (characterized against the old page)."""

from __future__ import annotations

import pytest

from lightfall.ui.preferences.proxy_settings import ProxySettingsPlugin

EXPECTED_PROXY = {
    "proxy_enabled": True,
    "proxy_type": "http",
    "proxy_host": "proxy.example.org",
    "proxy_port": 8080,
    "proxy_auto_detect": True,
}


class RecordingPrefs:
    def __init__(self):
        self.calls: list[tuple] = []

    def get(self, key, default=None):
        return default

    def set(self, key, value, **kwargs):
        self.calls.append((key, value))


@pytest.fixture
def prefs(monkeypatch):
    from lightfall.ui.preferences.manager import PreferencesManager

    rec = RecordingPrefs()
    monkeypatch.setattr(PreferencesManager, "get_instance", classmethod(lambda cls: rec))
    return rec


def test_proxy_writes_same_keys(qapp, prefs):
    page = ProxySettingsPlugin()
    page.create_widget()
    page.load_settings()
    page._enabled_checkbox.setChecked(True)
    page._type_combo.setCurrentIndex(page._type_combo.findData("http"))
    page._host_edit.setText("proxy.example.org")
    page._port_spin.setValue(8080)
    page._auto_detect_checkbox.setChecked(True)
    page.save_settings()
    assert dict(prefs.calls) == EXPECTED_PROXY
