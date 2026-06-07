"""Bluesky panel exposes its plan actions as title-bar actions."""

import pytest
from PySide6.QtWidgets import QToolBar

from lightfall.ui.panels.bluesky_panel import BlueskyPanel


@pytest.fixture()
def panel(qtbot):
    p = BlueskyPanel()
    qtbot.addWidget(p)
    return p


class TestBlueskyTitleBarActions:
    def test_three_title_bar_actions(self, panel):
        texts = [a.text() for a in panel.title_bar_actions]
        assert texts == ["New Plan", "Refresh", "Open Folder"]

    def test_actions_have_icons(self, panel):
        assert all(not a.icon().isNull() for a in panel.title_bar_actions)

    def test_no_embedded_toolbar(self, panel):
        assert panel.findChildren(QToolBar) == []
