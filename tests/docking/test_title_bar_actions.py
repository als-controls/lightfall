"""Tests for the BasePanel title-bar actions API."""

from PySide6.QtGui import QAction

from lightfall.ui.panels.base import BasePanel, PanelMetadata


class _ActionsInSetupPanel(BasePanel):
    """Panel that registers an action during _setup_ui (common pattern)."""

    panel_metadata = PanelMetadata(id="test.panels.actions", name="Actions")

    def _setup_ui(self):
        action = QAction("Refresh", self)
        self.add_title_bar_action(action)


class TestTitleBarActions:
    def test_default_empty(self, qtbot):
        class _Plain(BasePanel):
            panel_metadata = PanelMetadata(id="test.plain", name="Plain")

        panel = _Plain()
        qtbot.addWidget(panel)
        assert panel.title_bar_actions == []

    def test_add_action_emits_signal(self, qtbot):
        class _Plain2(BasePanel):
            panel_metadata = PanelMetadata(id="test.plain2", name="Plain2")

        panel = _Plain2()
        qtbot.addWidget(panel)
        action = QAction("Go", panel)
        with qtbot.waitSignal(panel.title_bar_actions_changed):
            panel.add_title_bar_action(action)
        assert panel.title_bar_actions == [action]

    def test_actions_addable_during_setup_ui(self, qtbot):
        panel = _ActionsInSetupPanel()
        qtbot.addWidget(panel)
        assert len(panel.title_bar_actions) == 1

    def test_returned_list_is_a_copy(self, qtbot):
        panel = _ActionsInSetupPanel()
        qtbot.addWidget(panel)
        panel.title_bar_actions.clear()
        assert len(panel.title_bar_actions) == 1
