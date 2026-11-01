"""Assistant Tools page characterization (old page), then the scope-aware catalog page."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from lightfall.plugins.tool_plugin import ToolPlugin
from lightfall.ui.panels.claude.tool_registry import ToolRegistry


class _Tool(ToolPlugin):
    _name = "alpha"
    _category = "general"
    _default = True

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return f"{self._name} plugin"

    @property
    def category(self):
        return self._category

    @property
    def enabled_by_default(self):
        return self._default


def make_tool(name, category="general", default=True):
    cls = type(f"Tool_{name}", (_Tool,), {"_name": name, "_category": category, "_default": default})
    return cls()


def tools():
    return [make_tool("alpha"), make_tool("beta", "devices"), make_tool("gamma", "devices", default=False)]


@pytest.fixture
def registry():
    ToolRegistry.reset_instance()
    reg = ToolRegistry.get_instance()
    for t in tools():
        reg.register(t)
    yield reg
    ToolRegistry.reset_instance()


class OldPrefs:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.sets: list[tuple] = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, **kw):
        self.sets.append((key, value))
        self.values[key] = value

    def remove(self, key, **kw):
        self.values.pop(key, None)


def test_old_page_characterization(qtbot, monkeypatch, registry):
    """What the pre-catalog page showed and wrote when two tools were toggled.

    It wrote two lists: ``disabled_tool_plugins`` and ``forced_enabled_tool_plugins``.
    The catalog page writes ``tools.enabled`` (a dict): each newly disabled default-on tool
    becomes ``{name: False}``; a default-off tool switched on becomes ``{name: True}``.
    """
    from lightfall.ui.preferences.tool_settings import ClaudeToolsSettingsPlugin, SkillSettingsPlugin

    assert SkillSettingsPlugin is ClaudeToolsSettingsPlugin  # alias keeps working
    prefs = OldPrefs({"disabled_tool_plugins": [], "forced_enabled_tool_plugins": []})
    monkeypatch.setattr(
        "lightfall.ui.preferences.manager.PreferencesManager.get_instance", classmethod(lambda cls: prefs)
    )
    page = ClaudeToolsSettingsPlugin()
    page.create_widget()
    page.load_settings()
    model = page._model
    rows = [
        (model.data(model.index(r, 0)), model.data(model.index(r, 0), Qt.ItemDataRole.CheckStateRole),
         model.data(model.index(r, 1)), model.data(model.index(r, 2)))
        for r in range(model.rowCount())
    ]
    assert rows == [  # sorted by category, then display name
        ("Beta", Qt.CheckState.Checked, "Devices", "beta plugin"),
        ("Gamma", Qt.CheckState.Unchecked, "Devices", "gamma plugin"),
        ("Alpha", Qt.CheckState.Checked, "General", "alpha plugin"),
    ]
    by_name = {r[0]: i for i, r in enumerate(rows)}
    model.setData(model.index(by_name["Alpha"], 0), Qt.CheckState.Unchecked.value,
                  Qt.ItemDataRole.CheckStateRole)
    model.setData(model.index(by_name["Gamma"], 0), Qt.CheckState.Checked.value,
                  Qt.ItemDataRole.CheckStateRole)
    page.save_settings()
    assert prefs.sets == [("disabled_tool_plugins", ["alpha"]),
                          ("forced_enabled_tool_plugins", ["gamma"])]
    # => new page equivalent: {"alpha": False, "gamma": True}
