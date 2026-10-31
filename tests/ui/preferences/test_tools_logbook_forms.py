"""External Tools and Logbook pages: the keys they write (characterized against the old pages)."""

from __future__ import annotations

import pytest

from lightfall.ui.preferences.editor_settings import ExternalToolsSettingsPlugin
from lightfall.ui.preferences.logbook_settings import LogbookSettingsPlugin

EXPECTED_TOOLS = {"code_editor": "pycharm", "suppress_pycharm_warning": True}
EXPECTED_LOGBOOK = {
    "logbook_enabled": True,
    "logbook_url": "http://logbook.example.org:9000",
    "logbook_offline_only": True,
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


def test_external_tools_writes_same_keys(qapp, prefs):
    page = ExternalToolsSettingsPlugin()
    page.create_widget()
    page.load_settings()
    page._editor_combo.setCurrentIndex(page._editor_combo.findData("pycharm"))
    page._suppress_pycharm_warning_check.setChecked(True)
    page.save_settings()
    assert dict(prefs.calls) == EXPECTED_TOOLS


def test_logbook_writes_same_keys(qapp, prefs):
    page = LogbookSettingsPlugin()
    page.create_widget()
    page.load_settings()
    page._enabled_check.setChecked(True)
    page._url_edit.setText("http://logbook.example.org:9000")
    page._offline_check.setChecked(True)
    page.save_settings()
    assert dict(prefs.calls) == EXPECTED_LOGBOOK
