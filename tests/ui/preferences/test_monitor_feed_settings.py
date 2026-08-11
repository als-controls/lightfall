from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from lightfall.ui.preferences import monitor_settings as ms


class _FakePrefs:
    def __init__(self, data: dict):
        self._data = dict(data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class _FakeFeed:
    def __init__(self, name: str, default_interval_s: float = 30.0):
        self.name = name
        self.default_interval_s = default_interval_s


class _FakePlugin:
    def __init__(self, name: str, feeds: list[_FakeFeed]):
        self.name = name
        self._feeds = feeds

    def create_feeds(self):
        return self._feeds


class _FakeRegistry:
    def __init__(self, plugins: list[_FakePlugin]):
        self._plugins = plugins

    def get_plugins(self):
        return list(self._plugins)

    def enabled_plugins(self):
        return list(self._plugins)


@pytest.fixture
def fake_prefs(monkeypatch):
    store = _FakePrefs({})
    monkeypatch.setattr(ms.PreferencesManager, "get_instance",
                        classmethod(lambda cls: store))
    return store


@pytest.fixture
def fake_registry(monkeypatch):
    feed_a = _FakeFeed("feed_a", default_interval_s=15.0)
    feed_b = _FakeFeed("feed_b", default_interval_s=45.0)
    plugin = _FakePlugin("plugin_x", [feed_a, feed_b])
    registry = _FakeRegistry([plugin])
    monkeypatch.setattr(ms.MonitorRegistry, "get_instance",
                        classmethod(lambda cls: registry))
    return registry


def _model_row_for(model, feed_name):
    for row in range(model.rowCount()):
        idx = model.index(row, 0)
        if model.data(idx, Qt.ItemDataRole.DisplayRole) == feed_name or \
           model._feeds[row].name == feed_name:
            return row
    raise AssertionError(f"feed {feed_name} not found")


def test_initial_check_state_honors_disabled_list(fake_prefs, fake_registry):
    fake_prefs.set("disabled_monitor_feeds", ["feed_b"])
    model = ms.MonitorFeedTableModel()
    model.refresh()
    model.load_from_prefs()

    row_a = _model_row_for(model, "feed_a")
    row_b = _model_row_for(model, "feed_b")

    idx_a = model.index(row_a, 0)
    idx_b = model.index(row_b, 0)
    assert model.data(idx_a, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert model.data(idx_b, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked


def test_toggle_and_save_writes_correct_prefs(fake_prefs, fake_registry):
    model = ms.MonitorFeedTableModel()
    model.refresh()
    model.load_from_prefs()

    row_a = _model_row_for(model, "feed_a")
    idx_a = model.index(row_a, 0)
    model.setData(idx_a, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
    model.save_to_prefs()

    assert fake_prefs.get("disabled_monitor_feeds") == ["feed_a"]


def test_severity_setdata_rejects_invalid_accepts_valid(fake_prefs, fake_registry):
    model = ms.MonitorFeedTableModel()
    model.refresh()
    model.load_from_prefs()

    row_a = _model_row_for(model, "feed_a")
    idx = model.index(row_a, 3)

    assert model.setData(idx, "loud", Qt.ItemDataRole.EditRole) is False
    assert model.setData(idx, "critical", Qt.ItemDataRole.EditRole) is True
    assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "critical"


def test_interval_setdata_persists_as_int(fake_prefs, fake_registry):
    model = ms.MonitorFeedTableModel()
    model.refresh()
    model.load_from_prefs()

    row_a = _model_row_for(model, "feed_a")
    idx = model.index(row_a, 2)

    assert model.setData(idx, "7", Qt.ItemDataRole.EditRole) is True
    model.save_to_prefs()
    assert fake_prefs.get("monitor_feed_intervals") == {"feed_a": 7}


def test_interval_setdata_rejects_non_positive_and_non_numeric(fake_prefs, fake_registry):
    model = ms.MonitorFeedTableModel()
    model.refresh()
    model.load_from_prefs()

    row_a = _model_row_for(model, "feed_a")
    idx = model.index(row_a, 2)

    assert model.setData(idx, "0", Qt.ItemDataRole.EditRole) is False
    assert model.setData(idx, "-5", Qt.ItemDataRole.EditRole) is False
    assert model.setData(idx, "abc", Qt.ItemDataRole.EditRole) is False


def test_severity_setdata_explicit_default_omits_from_saved_dict(fake_prefs, fake_registry):
    model = ms.MonitorFeedTableModel()
    model.refresh()
    model.load_from_prefs()

    row_a = _model_row_for(model, "feed_a")
    idx = model.index(row_a, 3)

    assert model.setData(idx, "info", Qt.ItemDataRole.EditRole) is True
    model.save_to_prefs()
    saved = fake_prefs.get("monitor_feed_advisor_severity")
    assert "feed_a" not in saved


def test_interval_setdata_explicit_default_omits_from_saved_dict(fake_prefs, fake_registry):
    model = ms.MonitorFeedTableModel()
    model.refresh()
    model.load_from_prefs()

    row_a = _model_row_for(model, "feed_a")
    idx = model.index(row_a, 2)

    # feed_a's default_interval_s is 15.0 (see _FakeFeed construction above)
    assert model.setData(idx, "15", Qt.ItemDataRole.EditRole) is True
    model.save_to_prefs()
    saved = fake_prefs.get("monitor_feed_intervals")
    assert "feed_a" not in saved


def test_has_changes_flips(fake_prefs, fake_registry):
    model = ms.MonitorFeedTableModel()
    model.refresh()
    model.load_from_prefs()

    assert model.has_changes() is False

    row_a = _model_row_for(model, "feed_a")
    idx_a = model.index(row_a, 0)
    model.setData(idx_a, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
    assert model.has_changes() is True

    model.save_to_prefs()
    model.load_from_prefs()
    assert model.has_changes() is False
