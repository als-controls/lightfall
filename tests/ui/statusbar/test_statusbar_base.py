"""StatusBarPlugin base: set_state renders themed qtawesome icons and follows theme changes."""

from __future__ import annotations

from typing import ClassVar

import pytest
from PySide6.QtWidgets import QLabel

from lightfall.plugins.statusbar_plugin import (
    COLOR_ROLES,
    StatusBarPlugin,
    StatusBarPluginMetadata,
    icon_like_chars,
)
from lightfall.utils.logging import logger
from tests.updates.fakes import FakeTheme


class Plain(StatusBarPlugin):
    metadata: ClassVar[StatusBarPluginMetadata] = StatusBarPluginMetadata(
        id="tests.statusbar.plain", name="Plain")

    @property
    def name(self) -> str:
        return "plain"

    def update(self) -> None:
        pass

    def connect_signals(self) -> None:
        pass

    def disconnect_signals(self) -> None:
        pass


class CustomWidget(Plain):
    """Overrides create_widget without calling super (the manager must still theme it)."""

    def __init__(self) -> None:
        super().__init__()
        self.theme_changes = 0

    def create_widget(self, parent=None):
        self._widget = QLabel(parent)
        return self._widget

    def on_theme_changed(self) -> None:
        self.theme_changes += 1


def make(qtbot, cls=Plain):
    plugin = cls()
    plugin._theme_manager = FakeTheme()
    qtbot.addWidget(plugin.create_widget())
    return plugin


@pytest.fixture
def warnings():
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="WARNING")
    yield lines
    logger.remove(sink)


def test_set_state_renders_icon_text_color_tooltip(qtbot):
    plugin = make(qtbot)
    plugin.set_state("3 queued", icon="mdi6.snail", role="warning", tooltip="tip")
    assert plugin._button.text() == "3 queued"
    assert not plugin._button.icon().isNull()
    assert "#fb8c00" in plugin._button.styleSheet()
    assert plugin._button.toolTip() == "tip"
    data = plugin.get_introspection_data()
    assert data["icon"] == "mdi6.snail" and data["color_role"] == "warning"


def test_theme_change_rerenders_last_state(qtbot):
    plugin = make(qtbot)
    plugin.set_state("x", icon="mdi6.snail", role="info")
    plugin._theme_manager.switch(info="#90caf9", warning="#ffcc80")
    assert "#90caf9" in plugin._button.styleSheet()


def test_no_role_uses_default_text_color(qtbot):
    plugin = make(qtbot)
    plugin.set_state("12 MB", icon="mdi6.memory")
    assert plugin._button.styleSheet() == ""
    assert not plugin._button.icon().isNull()


def test_icon_only_and_cleared_state(qtbot):
    from PySide6.QtCore import Qt

    plugin = make(qtbot)
    plugin.set_state("", icon="mdi6.database", role="success")
    assert plugin._button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    plugin.set_state("Off")
    assert plugin._button.icon().isNull()
    assert plugin._button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextOnly


def test_glyph_in_label_warns_once(qtbot, warnings):
    plugin = make(qtbot)
    plugin.set_text("⏳ 2 tasks")
    plugin.set_text("⏳ 3 tasks")
    assert len([w for w in warnings if "symbol characters" in w]) == 1
    assert plugin._button.text() == "⏳ 3 tasks"  # still shown: never break the bar


def test_raw_set_color_is_deprecated(qtbot, warnings):
    plugin = make(qtbot)
    plugin.set_color("#ff0000")
    assert "#ff0000" in plugin._button.styleSheet()
    assert any("set_color" in w and "deprecated" in w for w in warnings)


def test_unknown_role_and_bad_icon_warn_but_render(qtbot, warnings):
    plugin = make(qtbot)
    plugin.set_state("x", icon="mdi6.no-such-icon-xyz", role="purple")
    assert plugin._button.text() == "x"
    assert plugin._button.icon().isNull()
    assert any("unknown color role" in w for w in warnings)
    assert any("could not load icon" in w for w in warnings)


def test_icon_like_chars():
    assert icon_like_chars("⬆ 2 updates") == ["⬆"]
    assert icon_like_chars("✓ done \U0001F680") == ["✓", "\U0001F680"]
    # Letters and punctuation are fine.
    assert icon_like_chars("12 µm — Connecting… · a|b") == []


def test_roles_exist_on_theme_colors():
    from lightfall_utils.theming.manager import ThemeColors

    colors = ThemeColors()
    assert all(getattr(colors, role) for role in COLOR_ROLES)


def test_manager_attaches_theme_for_custom_widget_and_detaches(qtbot):
    from PySide6.QtWidgets import QStatusBar

    from lightfall.ui.statusbar.manager import StatusBarManager

    bar = QStatusBar()
    qtbot.addWidget(bar)
    manager = StatusBarManager(bar)
    plugin = CustomWidget()
    plugin._theme_manager = FakeTheme()
    assert manager.add_plugin(plugin)
    plugin._theme_manager.switch(info="#000001", warning="#000002")
    assert plugin.theme_changes == 1
    manager.remove_plugin(plugin.metadata.id)
    plugin._theme_manager.switch(info="#000003", warning="#000004")
    assert plugin.theme_changes == 1
