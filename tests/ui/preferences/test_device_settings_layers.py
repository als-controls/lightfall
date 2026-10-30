"""Device page over layered rows: origins, the Save-to target, inherited rows."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import DEVICE_BACKENDS_KEY, EXCLUSIVE_RESTORE_KEY
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


def _from(page):
    return [page._table.item(i, device_settings._COL_FROM).text()
            for i in range(page._table.rowCount())]


def _row_writes(prefs):
    return [(scope, value) for key, scope, value in prefs.writes if key == DEVICE_BACKENDS_KEY]


def test_table_shows_where_each_row_comes_from(make_page):
    prefs = LayeredPrefs(["user:u1", "local"], **{BRANCH: {"b": row("b")},
                                                  "local": {"c": row("c")},
                                                  "user:u1": {"u": row("u")}})
    page = make_page(prefs)
    assert [r.id for r in page._rows] == ["b", "c", "u"]
    assert _from(page) == ["7.0.1.1 (branch)", "This console", "Me"]


def test_default_target_is_the_branch_for_a_maintainer_else_this_console(make_page):
    page = make_page(LayeredPrefs(["user:u1", "local", BRANCH]))
    assert page._target() == BRANCH
    page = make_page(LayeredPrefs(["user:u1", "local"]))
    assert page._target() == "local"
    page = make_page(LayeredPrefs(["local"]))
    assert page._target() == "local" and not page._target_combo.isEnabled()


def test_saving_writes_only_the_target_layer(make_page):
    prefs = LayeredPrefs(["user:u1", "local"], **{BRANCH: {"b": row("b")}})
    page = make_page(prefs)
    page._add_row("mock")
    page.save_settings()
    ((scope, value),) = _row_writes(prefs)
    assert scope == "local"
    assert list(value) == [page._rows[1].id]              # the branch row is not copied
    assert prefs.layers[BRANCH] == {"b": row("b")}


def test_unchanged_page_writes_nothing(make_page):
    prefs = LayeredPrefs(["user:u1", "local"], **{BRANCH: {"b": row("b")},
                                                  "local": {"c": row("c")}})
    make_page(prefs).save_settings()
    assert _row_writes(prefs) == []


def test_switching_an_inherited_row_off_overrides_it_at_the_target(make_page):
    prefs = LayeredPrefs(["user:u1", "local"], **{BRANCH: {"b": row("b")}})
    page = make_page(prefs)
    page._target_combo.setCurrentIndex(page._target_combo.findData("user:u1"))
    page._table.item(0, device_settings._COL_ENABLED).setCheckState(Qt.CheckState.Unchecked)
    page.save_settings()
    assert _row_writes(prefs) == [("user:u1", {"b": row("b", enabled=False)})]


def test_inherited_row_cannot_be_removed(make_page, monkeypatch):
    shown = []
    monkeypatch.setattr(device_settings.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    prefs = LayeredPrefs(["user:u1", "local"], **{BRANCH: {"b": row("b")},
                                                  "local": {"c": row("c")}})
    page = make_page(prefs)
    page._select(0)
    page._remove_selected()
    assert [r.id for r in page._rows] == ["b", "c"]
    assert "7.0.1.1 (branch)" in shown[0]
    page._select(1)
    page._remove_selected()                                # owned by the target: removable
    assert [r.id for r in page._rows] == ["b"]
    page.save_settings()
    assert _row_writes(prefs) == [("local", {})]


def test_exclusive_restore_goes_to_the_same_target(make_page):
    prefs = LayeredPrefs(["user:u1", "local", BRANCH])
    page = make_page(prefs)
    page._restore_ids = ["a"]
    page.save_settings()
    assert (EXCLUSIVE_RESTORE_KEY, BRANCH, {"a": True}) in prefs.writes
