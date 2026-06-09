"""Tests for the BasePanel title-bar actions API."""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QToolButton

from lightfall.ui.docking.widget import PanelTitleBar
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


class TestAddTitleBarButtonHelper:
    def test_plain_button(self, qtbot):
        class _P(BasePanel):
            panel_metadata = PanelMetadata(id="test.helper.plain", name="P")

        panel = _P()
        qtbot.addWidget(panel)
        fired = []
        action = panel.add_title_bar_button(
            "mdi6.plus", "New", lambda *_: fired.append(True)
        )
        assert action in panel.title_bar_actions
        assert action.toolTip() == "New"
        action.trigger()
        assert fired == [True]

    def test_checkable_button(self, qtbot):
        class _P(BasePanel):
            panel_metadata = PanelMetadata(id="test.helper.toggle", name="P")

        panel = _P()
        qtbot.addWidget(panel)
        action = panel.add_title_bar_button(
            "mdi6.auto-download", "Auto", checkable=True, checked=True
        )
        assert action.isCheckable() and action.isChecked()

    def test_menu_button(self, qtbot):
        class _P(BasePanel):
            panel_metadata = PanelMetadata(id="test.helper.menu", name="P")

        panel = _P()
        qtbot.addWidget(panel)
        menu = QMenu()
        menu.addAction("Created")
        menu.addAction("Updated")
        action = panel.add_title_bar_button("mdi6.sort", "Sort", menu=menu)
        assert action.menu() is menu


class TestTitleBarMenuRendering:
    def test_menu_action_renders_as_instant_popup(self, qtbot):
        """A title bar action carrying a menu becomes an InstantPopup button,
        while a plain action does not."""
        bar = PanelTitleBar("Title")
        qtbot.addWidget(bar)

        plain = QAction("Refresh", bar)
        menu = QMenu()
        menu.addAction("One")
        menu_action = QAction("Sort", bar)
        menu_action.setMenu(menu)
        bar.set_actions([plain, menu_action])

        instant = [
            b
            for b in bar.findChildren(QToolButton)
            if b.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
        ]
        # Exactly the menu-carrying action becomes an instant popup; the
        # plain action and the window buttons do not.
        assert len(instant) == 1
        # The menu must actually be attached to the button (a default action
        # would leave button.menu() == None and nothing would pop).
        assert instant[0].menu() is menu
