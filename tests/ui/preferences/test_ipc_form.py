"""IPC page: the keys it writes (characterized against the old page)."""

from __future__ import annotations

import pytest

from lightfall.ui.preferences import ipc_settings as mod
from lightfall.ui.preferences.ipc_settings import IPCSettingsPlugin

# The old page never writes ipc_topic_prefix (read-only, derived from the binding).
EXPECTED_IPC = {
    "ipc_nats_url": "nats://broker.example.org:4222",
    "ipc_display_name": "CMS Hutch",
    "ipc_use_local_nats": True,
    "ipc_local_nats_port": 4299,
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
    monkeypatch.setattr(mod, "resolve_nats_binary", lambda: "/x/nats-server")
    monkeypatch.setattr(mod, "nats_binary_version", lambda p: "2.14.2")
    return rec


def test_ipc_writes_same_keys(qapp, prefs):
    page = IPCSettingsPlugin()
    page.create_widget()
    page.load_settings()
    page._url_edit.setText("nats://broker.example.org:4222")
    page._display_name_edit.setText("CMS Hutch")
    page._local_enable_cb.setChecked(True)
    page._local_port_edit.setText("4299")
    page.save_settings()
    assert dict(prefs.calls) == EXPECTED_IPC
