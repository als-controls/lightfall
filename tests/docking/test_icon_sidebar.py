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
