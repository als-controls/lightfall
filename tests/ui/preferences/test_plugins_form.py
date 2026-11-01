"""Plugins page characterization (old page), then the scope-aware catalog page."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from lightfall.plugins.errors import PluginStatus
from lightfall.plugins.info import PluginInfo
from lightfall.plugins.registry import PluginRegistry


def _info(type_name, name, status=PluginStatus.READY, manifest="core"):
    return PluginInfo(
        type_name=type_name, name=name, import_path=f"x.{name}:C", manifest_name=manifest, status=status
    )


PLUGINS = [
    _info("tool", "alpha"),
    _info("tool", "beta", PluginStatus.FAILED_LOAD),
    _info("settings", "plugins"),
    _info("auth_provider", "local", PluginStatus.DISABLED),
]


class OldPrefs:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.sets: list[tuple] = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, **kw):
        self.sets.append((key, value))
        self.values[key] = value


def _patch_registry(monkeypatch, prefs):
    registry = SimpleNamespace(get_all=lambda: list(PLUGINS))
    services = SimpleNamespace(get=lambda cls: registry if cls is PluginRegistry else None)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance", classmethod(lambda cls: services)
    )
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: prefs)
    )


def test_old_page_characterization(qtbot, monkeypatch):
    """What the pre-catalog page showed and wrote when two plugins were toggled.

    It wrote ``disabled_plugins`` (a list). The catalog page writes ``plugins.enabled``
    (a dict), so the expected list translates: each newly disabled id becomes
    ``{id: False}``; a default-disabled plugin switched on becomes ``{id: True}``.
    """
    from lightfall.ui.preferences.plugin_settings import PluginSettingsPlugin

    prefs = OldPrefs({"disabled_plugins": ["auth_provider:local"]})
    _patch_registry(monkeypatch, prefs)
    page = PluginSettingsPlugin()
    page.create_widget()
    page.load_settings()
    model = page._model
    rows = [
        (model.data(model.index(r, 0)), model.data(model.index(r, 0), Qt.ItemDataRole.CheckStateRole),
         model.data(model.index(r, 1)), model.data(model.index(r, 2)), model.data(model.index(r, 3)))
        for r in range(model.rowCount())
    ]
    assert rows == [  # sorted by type, then name
        ("local", Qt.CheckState.Unchecked, "auth_provider", "DISABLED", "core"),
        ("plugins", Qt.CheckState.Checked, "settings", "READY", "core"),
        ("alpha", Qt.CheckState.Checked, "tool", "READY", "core"),
        ("beta", Qt.CheckState.Checked, "tool", "FAILED_LOAD", "core"),
    ]

    def row_of(name):
        return next(i for i, r in enumerate(rows) if r[0] == name)

    # Protected plugin cannot be unchecked.
    assert not model.setData(model.index(row_of("plugins"), 0), Qt.CheckState.Unchecked.value,
                             Qt.ItemDataRole.CheckStateRole)
    # Turn alpha off, turn the default-disabled local auth on.
    model.setData(model.index(row_of("alpha"), 0), Qt.CheckState.Unchecked.value,
                  Qt.ItemDataRole.CheckStateRole)
    model.setData(model.index(row_of("local"), 0), Qt.CheckState.Checked.value,
                  Qt.ItemDataRole.CheckStateRole)
    page.save_settings()
    assert prefs.sets == [("disabled_plugins", ["tool:alpha"])]
    # => new page equivalent: {"tool:alpha": False, "auth_provider:local": True}
