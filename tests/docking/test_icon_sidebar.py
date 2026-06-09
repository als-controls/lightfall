"""Tests for IconStripSidebar ordering and context menu."""

from lightfall.ui.docking.icon_sidebar import IconStripSidebar


class TestOrderedPanelIds:
    def test_empty(self, qtbot):
        sidebar = IconStripSidebar()
        qtbot.addWidget(sidebar)
        assert sidebar.ordered_panel_ids() == []

    def test_visual_order_top_then_bottom(self, qtbot):
        sidebar = IconStripSidebar()
        qtbot.addWidget(sidebar)
        sidebar.add_panel_button("top.a", "mdi6.alpha-a", "A")
        sidebar.add_panel_button("top.b", "mdi6.alpha-b", "B")
        sidebar.add_stretch()
        sidebar.add_panel_button("bot.c", "mdi6.alpha-c", "C")
        assert sidebar.ordered_panel_ids() == ["top.a", "top.b", "bot.c"]

    def test_sorted_insert_respected(self, qtbot):
        sidebar = IconStripSidebar()
        qtbot.addWidget(sidebar)
        sidebar.add_panel_button("top.b", "mdi6.alpha-b", "B")
        sidebar.add_stretch()
        sidebar.insert_panel_button_sorted(
            "top.a", "mdi6.alpha-a", "A", sidebar_order=-1, section="top"
        )
        assert sidebar.ordered_panel_ids() == ["top.a", "top.b"]


class TestContextMenu:
    def test_remove_signal_emitted(self, qtbot, monkeypatch):
        from PySide6.QtCore import QPoint

        sidebar = IconStripSidebar()
        qtbot.addWidget(sidebar)
        button = sidebar.add_panel_button("top.a", "mdi6.alpha-a", "A")

        # Patch _exec_context_menu to auto-choose the first action without
        # blocking (QMenu.exec is a C++ method that can't be patched via
        # monkeypatch.setattr on the class in PySide6).
        monkeypatch.setattr(
            sidebar,
            "_exec_context_menu",
            lambda menu, pos: menu.actions()[0],
        )
        with qtbot.waitSignal(sidebar.panel_remove_requested) as blocker:
            button.customContextMenuRequested.emit(QPoint(5, 5))
        assert blocker.args == ["top.a"]

    def test_menu_dismissed_no_signal(self, qtbot, monkeypatch):
        from PySide6.QtCore import QPoint

        sidebar = IconStripSidebar()
        qtbot.addWidget(sidebar)
        button = sidebar.add_panel_button("top.a", "mdi6.alpha-a", "A")
        monkeypatch.setattr(
            sidebar,
            "_exec_context_menu",
            lambda menu, pos: None,
        )
        with qtbot.assertNotEmitted(sidebar.panel_remove_requested):
            button.customContextMenuRequested.emit(QPoint(5, 5))
