"""Settings -> Devices: an exclusive row switches the others off; mode changes restart."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt

from lightfall.devices.backend_types import session
from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import BackendRow
from lightfall.ui.preferences import device_settings
from tests.devices.backend_types.excl_type import ExclType, no_session_mode  # noqa: F401

_COL_ENABLED = device_settings._COL_ENABLED


@pytest.fixture()
def plugin(qtbot, monkeypatch, no_session_mode):  # noqa: F811
    BackendTypeRegistry.reset()
    BackendTypeRegistry.get_instance().register(ExclType())
    monkeypatch.setattr(device_settings, "_device_backend_plugin_names", lambda: [])
    p = device_settings.DeviceSettingsPlugin()
    widget = p.create_widget()
    qtbot.addWidget(widget)
    yield p
    BackendTypeRegistry.reset()


def _set_rows(p, rows):
    p._rows = rows
    p._refresh_table()


def _tick(p, index, checked=True):
    state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    p._table.item(index, _COL_ENABLED).setCheckState(state)


def _confirm(monkeypatch, answer):
    asked = []
    monkeypatch.setattr(
        device_settings.DeviceSettingsPlugin,
        "_confirm_exclusive",
        lambda self, name, n: asked.append((name, n)) or answer,
    )
    return asked


def _rows():
    return [
        BackendRow("mock", "a"),
        BackendRow("mock", "b"),
        BackendRow("excl", "sim", enabled=False, config={"addr": "127.0.0.1"}),
    ]


def test_ticking_exclusive_disables_others_and_remembers(plugin, monkeypatch):
    asked = _confirm(monkeypatch, True)
    rows = _rows()
    _set_rows(plugin, rows)
    _tick(plugin, 2)
    assert asked == [("Excl", 2)]
    assert [r.enabled for r in plugin._rows] == [False, False, True]
    assert plugin._restore_ids == [rows[0].id, rows[1].id]


def test_cancel_keeps_exclusive_off(plugin, monkeypatch):
    _confirm(monkeypatch, False)
    _set_rows(plugin, _rows())
    _tick(plugin, 2)
    assert [r.enabled for r in plugin._rows] == [True, True, False]
    assert plugin._table.item(2, _COL_ENABLED).checkState() == Qt.CheckState.Unchecked


def test_other_rows_not_checkable_while_exclusive_on(plugin, monkeypatch):
    _confirm(monkeypatch, True)
    _set_rows(plugin, _rows())
    _tick(plugin, 2)
    for index in (0, 1):
        flags = plugin._table.item(index, _COL_ENABLED).flags()
        assert not flags & Qt.ItemFlag.ItemIsUserCheckable
        assert not flags & Qt.ItemFlag.ItemIsEnabled
    assert "Excl: 127.0.0.1" in plugin._exclusive_hint.text()
    assert not plugin._exclusive_hint.isHidden()


def test_unticking_exclusive_restores_previous_rows(plugin, monkeypatch):
    _confirm(monkeypatch, True)
    rows = _rows()
    rows[1].enabled = False  # b was already off: it stays off afterwards
    _set_rows(plugin, rows)
    _tick(plugin, 2)
    _tick(plugin, 2, checked=False)
    assert [r.enabled for r in plugin._rows] == [True, False, False]
    assert plugin._restore_ids == []
    assert plugin._exclusive_hint.isHidden()


def _save_spies(monkeypatch, plan_active=False):
    applied, restarts, asks, shown = [], [], [], []
    monkeypatch.setattr(device_settings, "_plan_active", lambda: plan_active)
    monkeypatch.setattr(
        "lightfall.devices.backend_types.startup.apply_backend_rows",
        lambda rows, ctx=None: applied.append(rows) or True,
    )
    monkeypatch.setattr("lightfall.utils.restart.request_restart", lambda: restarts.append(1))
    monkeypatch.setattr(
        device_settings.DeviceSettingsPlugin,
        "_ask_restart",
        lambda self, label, entering: asks.append((label, entering)) or True,
    )
    monkeypatch.setattr(
        device_settings.QMessageBox, "information", lambda *a, **k: shown.append(a)
    )
    prefs = MagicMock()
    prefs.get.side_effect = lambda key, default=None: default
    return prefs, applied, restarts, asks, shown


def test_save_across_mode_boundary_skips_live_apply_and_offers_restart(plugin, monkeypatch):
    prefs, applied, restarts, asks, _ = _save_spies(monkeypatch)
    _set_rows(plugin, [BackendRow("excl", "sim", config={"addr": "127.0.0.1"})])
    plugin._apply_rows_live(prefs)
    assert applied == []
    assert asks == [("Excl: 127.0.0.1", True)]
    assert restarts == [1]


def test_save_across_boundary_during_plan_shows_plan_message_only(plugin, monkeypatch):
    prefs, applied, restarts, asks, shown = _save_spies(monkeypatch, plan_active=True)
    _set_rows(plugin, [BackendRow("excl", "sim", config={"addr": "127.0.0.1"})])
    plugin._apply_rows_live(prefs)
    assert applied == [] and asks == [] and restarts == []
    assert len(shown) == 1


def test_save_within_same_mode_applies_live(plugin, monkeypatch):
    row = BackendRow("excl", "sim", config={"addr": "127.0.0.1"})
    session.set_session_mode(
        session.resolve_session_mode([row], BackendTypeRegistry.get_instance())
    )
    prefs, applied, restarts, asks, _ = _save_spies(monkeypatch)
    edited = BackendRow("excl", "sim", config={"addr": "127.0.0.1", "note": "x"}, id=row.id)
    _set_rows(plugin, [edited])
    plugin._apply_rows_live(prefs)
    assert len(applied) == 1 and asks == [] and restarts == []


def test_leaving_mode_asks_with_current_label(plugin, monkeypatch):
    row = BackendRow("excl", "sim", config={"addr": "127.0.0.1"})
    session.set_session_mode(
        session.resolve_session_mode([row], BackendTypeRegistry.get_instance())
    )
    prefs, applied, restarts, asks, _ = _save_spies(monkeypatch)
    _set_rows(plugin, [BackendRow("mock", "mock")])
    plugin._apply_rows_live(prefs)
    assert applied == [] and asks == [("Excl: 127.0.0.1", False)]


def test_added_exclusive_row_starts_disabled(plugin):
    # I3: Add must not bypass the confirm-and-switch-off path.
    _set_rows(plugin, [BackendRow("mock", "a")])
    plugin._add_row("excl")
    assert [r.enabled for r in plugin._rows] == [True, False]


def test_duplicated_exclusive_row_starts_disabled(plugin, monkeypatch):
    _confirm(monkeypatch, True)
    _set_rows(plugin, _rows())
    _tick(plugin, 2)
    plugin._select(2)
    plugin._duplicate_selected()
    assert [r.enabled for r in plugin._rows] == [False, False, True, False]


def test_removing_enabled_exclusive_row_restores_others(plugin, monkeypatch):
    _confirm(monkeypatch, True)
    _set_rows(plugin, _rows())
    _tick(plugin, 2)
    plugin._select(2)
    plugin._remove_selected()
    assert [r.enabled for r in plugin._rows] == [True, True]
    assert plugin._restore_ids == []
