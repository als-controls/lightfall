"""Tests for remove-from-sidebar with persistence and restore."""

from PySide6.QtWidgets import QMainWindow

from lightfall.ui.docking.manager import DockingManager
from lightfall.ui.panels.registry import PanelRegistry

from .conftest import make_panel_class


def _register_deferred(docking, panel_id, **kwargs):
    cls = make_panel_class(panel_id, **kwargs)
    PanelRegistry.get_instance().register(cls)
    docking.register_deferred_panel(
        panel_id, cls.panel_metadata, cls.panel_metadata.default_area
    )
    return cls


class TestRemoveFromSidebar:
    def test_remove_hides_and_drops_button(self, qtbot, docking):
        _register_deferred(docking, "test.a")
        docking._instantiate_deferred_panel("test.a")
        docking.show_panel("test.a")
        docking.remove_panel_from_sidebar("test.a")
        assert "test.a" not in docking.icon_sidebar.ordered_panel_ids()
        assert not docking.get_dock_widget("test.a").isVisible()
        # Instance still registered with the docking manager
        assert docking.get_panel("test.a") is not None

    def test_remove_signal_from_sidebar_routes(self, qtbot, docking):
        _register_deferred(docking, "test.a")
        docking.icon_sidebar.panel_remove_requested.emit("test.a")
        assert "test.a" not in docking.icon_sidebar.ordered_panel_ids()

    def test_removed_persists_across_managers(self, qtbot, docking):
        _register_deferred(docking, "test.a")
        docking.remove_panel_from_sidebar("test.a")

        # Simulated restart: fresh manager, same (temp) QSettings
        window2 = QMainWindow()
        qtbot.addWidget(window2)
        manager2 = DockingManager(window2)
        manager2.initialize()
        assert manager2.is_panel_removed_from_sidebar("test.a")

        # Registering the deferred panel again must NOT create a button
        cls = make_panel_class("test.a")
        manager2.register_deferred_panel("test.a", cls.panel_metadata, "left")
        assert "test.a" not in manager2.icon_sidebar.ordered_panel_ids()

    def test_runtime_registration_suppressed(self, qtbot, docking):
        _register_deferred(docking, "test.a")
        docking.remove_panel_from_sidebar("test.a")
        # Simulate runtime plugin registration after popping the deferred
        # entries so that _on_panel_registered's already-known guard doesn't
        # fire — confirming the removed-set guard is what blocks the button.
        docking._deferred_panels.pop("test.a", None)
        docking._deferred_metadata.pop("test.a", None)
        cls2 = make_panel_class("test.a")
        docking._on_panel_registered("test.a", cls2.panel_metadata)
        assert "test.a" not in docking.icon_sidebar.ordered_panel_ids()

    def test_proactive_init_skips_removed(self, qtbot, docking):
        _register_deferred(docking, "test.a")
        docking.remove_panel_from_sidebar("test.a")
        docking.start_proactive_init()
        qtbot.wait(100)
        assert docking.is_panel_deferred("test.a")


class TestRestore:
    def test_restore_clears_flag_and_readds_button(self, qtbot, docking):
        _register_deferred(docking, "test.a")
        docking.remove_panel_from_sidebar("test.a")
        assert docking.restore_panel_to_sidebar("test.a") is True
        assert not docking.is_panel_removed_from_sidebar("test.a")
        assert "test.a" in docking.icon_sidebar.ordered_panel_ids()

    def test_restore_not_removed_returns_false(self, qtbot, docking):
        _register_deferred(docking, "test.a")
        assert docking.restore_panel_to_sidebar("test.a") is False

    def test_restore_instantiated_panel(self, qtbot, docking):
        _register_deferred(docking, "test.a")
        docking._instantiate_deferred_panel("test.a")
        docking.remove_panel_from_sidebar("test.a")
        docking.restore_panel_to_sidebar("test.a")
        assert "test.a" in docking.icon_sidebar.ordered_panel_ids()
