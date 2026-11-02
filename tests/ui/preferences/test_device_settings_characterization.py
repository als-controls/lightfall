"""Characterization of the old Devices page (deleted when the page moves to the form)."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import DEVICE_BACKENDS_KEY
from lightfall.ui.preferences import device_settings

BRANCH = "branch:7.0.1.1"


def row(rid, **over):
    return {"id": rid, "type": "mock", "name": rid, "enabled": True,
            "config": {"include_noisy": True}, **over}


class LayeredPrefs:
    """PreferencesManager stand-in with explicit layers, merged like ScopedBackend."""

    ORDER = (BRANCH, "local", "user:u1")

    def __init__(self, targets, **layers):
        self.layers = {scope: dict(items) for scope, items in layers.items()}
        self.targets = targets
        self.writes = []
        self.data = {}

    def _merged(self):
        out, origins = {}, {}
        for scope in self.ORDER:
            for rid, item in self.layers.get(scope, {}).items():
                out[rid], origins[rid] = item, scope
        return out, origins

    def get(self, key, default=None):
        if key == DEVICE_BACKENDS_KEY:
            merged, _ = self._merged()
            return merged or default
        return self.data.get(key, default)

    def set(self, key, value, scope=None):
        self.writes.append((key, scope, value))
        if key == DEVICE_BACKENDS_KEY:
            self.layers[scope] = dict(value)
        else:
            self.data[key] = value

    def remove(self, key, scope=None):
        self.writes.append((key, scope, None))

    def write_targets(self, key):
        return list(self.targets)

    def item_origins(self, key):
        return self._merged()[1]

    def layer_value(self, key, scope):
        if key == DEVICE_BACKENDS_KEY:
            return self.layers.get(scope)
        return None


@pytest.fixture()
def make_page(qtbot, monkeypatch):
    BackendTypeRegistry.reset()
    monkeypatch.setattr(device_settings, "_device_backend_plugin_names", lambda: [])
    monkeypatch.setattr(device_settings.DeviceSettingsPlugin, "_apply_rows_live",
                        lambda self, prefs: None)

    def _make(prefs):
        monkeypatch.setattr(device_settings.PreferencesManager, "get_instance", lambda: prefs)
        page = device_settings.DeviceSettingsPlugin()
        qtbot.addWidget(page.create_widget())
        page.load_settings()
        return page
    yield _make
    BackendTypeRegistry.reset()



def test_old_page_writes_connection_scalars_unscoped(make_page):
    prefs = LayeredPrefs(["local"])
    make_page(prefs).save_settings()
    scalars = [(k, v, s) for k, s, v in prefs.writes if k != DEVICE_BACKENDS_KEY]
    assert scalars == [
        ("device_instantiate_mode", "background", None),
        ("device_connection_timeout", 5.0, None),
        ("device_connect_on_startup", True, None),
        ("ca_tunnel_enabled", False, None),
        ("ca_tunnel_gateway", "localhost:5099", None),
    ]


def test_old_page_row_override_shape(make_page):
    prefs = LayeredPrefs(["user:u1", "local"], **{BRANCH: {"a": row("a")}})
    page = make_page(prefs)
    page._target_combo.setCurrentIndex(page._target_combo.findData("local"))
    page._table.item(0, device_settings._COL_ENABLED).setCheckState(Qt.CheckState.Unchecked)
    page.save_settings()
    assert [(s, v) for k, s, v in prefs.writes if k == DEVICE_BACKENDS_KEY] == [
        ("local", {"a": {**row("a"), "enabled": False}})]
