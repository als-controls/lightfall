"""Saving device settings applies rows live, except while a plan runs."""

from __future__ import annotations

from unittest.mock import MagicMock

from lightfall.ui.preferences import device_settings


def _plugin_with_spy(monkeypatch, plan_active: bool):
    applied, shown = [], []
    monkeypatch.setattr(device_settings, "_plan_active", lambda: plan_active)
    monkeypatch.setattr(
        "lightfall.devices.backend_types.startup.apply_backend_rows",
        lambda rows, ctx=None: applied.append(rows) or True,
    )
    monkeypatch.setattr(
        device_settings.QMessageBox, "information", lambda *a, **k: shown.append(a)
    )
    plugin = device_settings.DeviceSettingsPlugin()
    plugin._rows = []
    prefs = MagicMock()
    prefs.get.side_effect = lambda key, default=None: default
    return plugin, prefs, applied, shown


def test_save_applies_rows_live_when_idle(qapp, monkeypatch):
    plugin, prefs, applied, shown = _plugin_with_spy(monkeypatch, plan_active=False)

    plugin._apply_rows_live(prefs)

    assert applied == [[]] and shown == []


def test_save_does_not_apply_while_a_plan_runs(qapp, monkeypatch):
    plugin, prefs, applied, shown = _plugin_with_spy(monkeypatch, plan_active=True)

    plugin._apply_rows_live(prefs)

    assert applied == [] and len(shown) == 1
