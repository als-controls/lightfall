"""Tests for the reworked PanelTitleBar."""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QToolButton

from lightfall.ui.docking.widget import PanelTitleBar


def _shown_buttons(bar: PanelTitleBar) -> list[QToolButton]:
    return [
        b for b in bar.findChildren(QToolButton) if not b.isHidden()
    ]


class TestWindowButtons:
    def test_minimize_button_emits_close_requested(self, qtbot):
        bar = PanelTitleBar("T")
        qtbot.addWidget(bar)
        with qtbot.waitSignal(bar.close_requested):
            bar._minimize_btn.click()

    def test_not_closable_has_no_minimize(self, qtbot):
        bar = PanelTitleBar("T", closable=False)
        qtbot.addWidget(bar)
        assert not hasattr(bar, "_minimize_btn")

    def test_expand_button_emits(self, qtbot):
        bar = PanelTitleBar("T")
        qtbot.addWidget(bar)
        with qtbot.waitSignal(bar.expand_requested):
            bar._expand_btn.click()

    def test_redock_hidden_by_default(self, qtbot):
        bar = PanelTitleBar("T")
        qtbot.addWidget(bar)
        bar.show()
        assert bar._redock_btn.isHidden()
        assert not bar._expand_btn.isHidden()

    def test_set_floating_swaps_expand_and_redock(self, qtbot):
        bar = PanelTitleBar("T")
        qtbot.addWidget(bar)
        bar.show()
        bar.set_floating(True)
        assert not bar._redock_btn.isHidden()
        assert bar._expand_btn.isHidden()
        bar.set_floating(False)
        assert bar._redock_btn.isHidden()
        assert not bar._expand_btn.isHidden()

    def test_redock_button_emits(self, qtbot):
        bar = PanelTitleBar("T")
        qtbot.addWidget(bar)
        bar.set_floating(True)
        with qtbot.waitSignal(bar.redock_requested):
            bar._redock_btn.click()


class TestActionButtons:
    def test_no_actions_no_separator(self, qtbot):
        bar = PanelTitleBar("T")
        qtbot.addWidget(bar)
        bar.show()
        assert bar._separator.isHidden()
        assert bar._actions_layout.count() == 0

    def test_set_actions_creates_buttons(self, qtbot):
        bar = PanelTitleBar("T")
        qtbot.addWidget(bar)
        bar.show()
        a1 = QAction("One", bar)
        a2 = QAction("Two", bar)
        bar.set_actions([a1, a2])
        assert bar._actions_layout.count() == 2
        assert not bar._separator.isHidden()
        btn = bar._actions_layout.itemAt(0).widget()
        assert btn.defaultAction() is a1

    def test_set_actions_rebuilds(self, qtbot):
        bar = PanelTitleBar("T")
        qtbot.addWidget(bar)
        bar.set_actions([QAction("One", bar)])
        bar.set_actions([])
        assert bar._actions_layout.count() == 0

    def test_action_trigger_via_button(self, qtbot):
        bar = PanelTitleBar("T")
        qtbot.addWidget(bar)
        action = QAction("One", bar)
        bar.set_actions([action])
        with qtbot.waitSignal(action.triggered):
            bar._actions_layout.itemAt(0).widget().click()
