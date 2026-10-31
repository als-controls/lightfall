"""Tiled page: the keys it writes (characterized against the old page)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lightfall.ui.preferences.tiled_settings import TiledSettingsPlugin

# Same edits as on the old page, grouped by the scope the new page edits them at.
EXPECTED_BEAMLINE = {
    "tiled_enabled": True,
    "tiled_url": "https://tiled.example.org",
    "tiled_auth_mode": "api_key",
    "tiled_alshub_url": "https://alshub.example.org",
}
EXPECTED_LOCAL = {"tiled_api_key": "s3cret-key"}
EXPECTED_USER = {
    "tiled_session_key_lifetime": 86400,
    "tiled_access_override_esaf_id": "BLS-00480-001",
    "tiled_access_override_start": "2026-04-26T18:00:00+00:00",
    "tiled_access_override_end": "2026-04-27T02:00:00+00:00",
}


class RecordingPrefs:
    def __init__(self):
        self.calls: list[tuple] = []

    def get(self, key, default=None):
        return default

    def set(self, key, value, **kwargs):
        self.calls.append((key, value))


class FakeService:
    def configure(self, **kw):
        pass

    def connect_async(self):
        pass

    def disconnect_tiled(self):
        pass


@pytest.fixture
def old_env(monkeypatch):
    from lightfall.ui.preferences.manager import PreferencesManager

    rec = RecordingPrefs()
    monkeypatch.setattr(PreferencesManager, "get_instance", classmethod(lambda cls: rec))
    monkeypatch.setattr(
        "lightfall.services.tiled_service.TiledService.get_instance",
        classmethod(lambda cls: FakeService()),
    )
    monkeypatch.setattr(TiledSettingsPlugin, "_user_has_admin_or_staff", lambda self: True)
    return SimpleNamespace(prefs=rec)


def test_tiled_writes_same_keys(qapp, old_env):
    page = TiledSettingsPlugin()
    page.create_widget()
    page.load_settings()
    page._enabled_check.setChecked(True)
    page._url_edit.setText("https://tiled.example.org")
    page._auth_mode_combo.setCurrentIndex(page._auth_mode_combo.findData("api_key"))
    page._api_key_edit.setText("s3cret-key")
    page._key_lifetime_combo.setCurrentIndex(page._key_lifetime_combo.findData(86400))
    page._alshub_url_edit.setText("https://alshub.example.org")
    page._override_esaf_edit.setText("BLS-00480-001")
    page._override_start_edit.setText("2026-04-26T18:00:00+00:00")
    page._override_end_edit.setText("2026-04-27T02:00:00+00:00")
    page.save_settings()
    written = dict(old_env.prefs.calls)
    assert "tiled_beamline" not in written  # the old page never wrote the beamline
    assert written == {**EXPECTED_BEAMLINE, **EXPECTED_LOCAL, **EXPECTED_USER}
