"""Preferences dialog wraps each plugin page in its own vertical scroll area."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QWidget

from lightfall.ui.preferences.dialog import PreferencesDialog


class _FakePlugin:
    name = "fake"
    display_name = "Fake"
    category = "A"
    priority = 0
    icon = None

    def __init__(self):
        self.widget = None

    def create_widget(self, parent):
        self.widget = QWidget()
        self.widget.setMinimumHeight(2000)  # taller than the dialog
        return self.widget

    def load_settings(self):
        pass


@pytest.fixture
def fake_registry(monkeypatch):
    plugin = _FakePlugin()
    info = SimpleNamespace(instance=plugin)
    registry = SimpleNamespace(get_ready_by_type=lambda _t: [info])
    services = SimpleNamespace(get=lambda _cls: registry)
    monkeypatch.setattr(
        "lightfall.core.services.ServiceRegistry.get_instance",
        classmethod(lambda cls: services),
    )
    return plugin


def test_plugin_pages_are_scroll_areas(qtbot, fake_registry):
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)

    assert dialog._stack.count() == 1
    page = dialog._stack.widget(0)
    assert isinstance(page, QScrollArea)
    # The plugin's own widget is wrapped, not added directly.
    assert page.widget() is fake_registry.widget
    assert page.widgetResizable() is True
    assert (
        page.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert (
        page.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
