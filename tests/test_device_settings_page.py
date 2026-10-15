"""Devices settings page: rows of backends with per-type editors."""
from __future__ import annotations

import pytest

from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import DEVICE_BACKENDS_KEY
from lightfall.ui.preferences import device_settings


class FakePrefs:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


@pytest.fixture()
def prefs(monkeypatch):
    fake = FakePrefs()
    monkeypatch.setattr(device_settings.PreferencesManager, "get_instance", lambda: fake)
    monkeypatch.setattr("lightfall.devices.backend_types.csm.session_token", lambda: None)
    BackendTypeRegistry.reset()
    yield fake
    BackendTypeRegistry.reset()


@pytest.fixture()
def page(qtbot, prefs):
    plugin = device_settings.DeviceSettingsPlugin()
    widget = plugin.create_widget()
    qtbot.addWidget(widget)
    plugin.load_settings()
    return plugin


def _saved(prefs):
    return prefs.data[DEVICE_BACKENDS_KEY]


def test_fresh_config_shows_migrated_mock_and_does_not_save_on_load(page, prefs):
    assert [r.name for r in page._rows] == ["mock"]
    assert page._table.rowCount() == 1
    assert DEVICE_BACKENDS_KEY not in prefs.data


def test_add_two_happi_rows_edit_save_reload(qtbot, page, prefs):
    page._add_row("happi")
    page._add_row("happi")
    assert [r.name for r in page._rows] == ["mock", "happi", "happi-2"]
    page._select(2)
    editor = page._stack.currentWidget()
    editor._path.setText("/data/b.json")
    page.save_settings()

    saved = _saved(prefs)
    assert [(r["type"], r["name"]) for r in saved] == [
        ("mock", "mock"), ("happi", "happi"), ("happi", "happi-2")
    ]
    assert saved[2]["config"]["path"] == "/data/b.json"

    again = device_settings.DeviceSettingsPlugin()
    qtbot.addWidget(again.create_widget())
    again.load_settings()
    assert [r.config.get("path") for r in again._rows] == [None, "", "/data/b.json"]


def test_rename_via_name_field_updates_table(page):
    page._add_row("bcs")
    page._select(1)
    page._name_edit.setText("bcs-7011")
    assert page._rows[1].name == "bcs-7011"
    assert page._table.item(1, 1).text() == "bcs-7011"


def test_duplicate_name_fails_validation(page):
    page._add_row("happi")
    page._select(1)
    page._name_edit.setText("MOCK")
    assert any("unique" in e for e in page.validate())


def test_empty_name_fails_validation(page):
    page._select(0)
    page._name_edit.setText("  ")
    assert any("name" in e.lower() for e in page.validate())


def test_type_validation_prefixed_with_row_name(page):
    page._add_row("csm")  # no beamline chosen
    errors = page.validate()
    assert "csm: Beamline is required" in errors


def test_disabled_row_skips_type_validation(page):
    page._add_row("csm")
    page._rows[1].enabled = False
    page._refresh_table()
    assert page.validate() == []


def test_duplicate_and_remove(page):
    page._select(0)
    page._duplicate_selected()
    assert [r.name for r in page._rows] == ["mock", "mock-2"]
    assert page._rows[0].id != page._rows[1].id
    page._select(0)
    page._remove_selected()
    assert [r.name for r in page._rows] == ["mock-2"]


def test_enabled_checkbox_toggles_row(page):
    from PySide6.QtCore import Qt

    page._table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert page._rows[0].enabled is False


def test_unknown_type_row_survives_save(qtbot, prefs):
    prefs.data[DEVICE_BACKENDS_KEY] = [
        {"id": "a", "type": "plugin-x", "name": "x", "enabled": True, "config": {"k": 1}}
    ]
    plugin = device_settings.DeviceSettingsPlugin()
    qtbot.addWidget(plugin.create_widget())
    plugin.load_settings()
    assert "unavailable" in plugin._table.item(0, 2).text()
    plugin.save_settings()
    assert _saved(prefs) == [
        {"id": "a", "type": "plugin-x", "name": "x", "enabled": True, "config": {"k": 1}}
    ]


def test_saved_empty_list_stays_empty(qtbot, prefs):
    prefs.data[DEVICE_BACKENDS_KEY] = []
    plugin = device_settings.DeviceSettingsPlugin()
    qtbot.addWidget(plugin.create_widget())
    plugin.load_settings()
    assert plugin._rows == []
    plugin.save_settings()
    assert _saved(prefs) == []


def test_connection_settings_still_saved(page, prefs):
    page._connection_timeout_spin.setValue(7.5)
    page.save_settings()
    assert prefs.data["device_connection_timeout"] == 7.5
