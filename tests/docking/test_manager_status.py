"""Tests for DockingManager panel-status tracking and icon tinting."""

import pytest

from lightfall.ui.panels.base import PanelStatus
from lightfall.ui.panels.registry import PanelRegistry
from lightfall.ui.theme import ThemeManager

from .conftest import make_panel_class


def _register_deferred(docking, panel_id: str, **kwargs):
    """Register a panel class and a deferred sidebar entry for it."""
    cls = make_panel_class(panel_id, **kwargs)
    PanelRegistry.get_instance().register(cls)
    docking.register_deferred_panel(
        panel_id, cls.panel_metadata, cls.panel_metadata.default_area
    )
    return cls


class TestStatusTracking:
    def test_default_uninitialized(self, docking):
        _register_deferred(docking, "test.p1")
        assert docking.get_panel_status("test.p1") is PanelStatus.UNINITIALIZED

    def test_instantiation_sets_success(self, docking):
        _register_deferred(docking, "test.p1")
        panel = docking._instantiate_deferred_panel("test.p1")
        assert panel is not None
        assert docking.get_panel_status("test.p1") is PanelStatus.SUCCESS

    def test_failed_instantiation_sets_error(self, docking):
        # Deferred entry without a registered class -> registry.create
        # returns None.
        cls = make_panel_class("test.broken")
        docking.register_deferred_panel(
            "test.broken", cls.panel_metadata, "left"
        )
        panel = docking._instantiate_deferred_panel("test.broken")
        assert panel is None
        assert docking.get_panel_status("test.broken") is PanelStatus.ERROR

    def test_panel_set_status_propagates(self, docking):
        _register_deferred(docking, "test.p1")
        panel = docking._instantiate_deferred_panel("test.p1")
        panel.set_status(PanelStatus.WARNING)
        assert docking.get_panel_status("test.p1") is PanelStatus.WARNING

    def test_remove_panel_clears_status_and_override(self, docking):
        _register_deferred(docking, "test.p1")
        panel = docking._instantiate_deferred_panel("test.p1")
        panel.set_sidebar_icon(color="#123456")
        panel.set_status(PanelStatus.WARNING)
        docking.remove_panel("test.p1")
        assert docking.get_panel_status("test.p1") is PanelStatus.UNINITIALIZED
        assert "test.p1" not in docking._icon_color_overrides


class TestIconTinting:
    def _button_color_calls(self, docking, monkeypatch):
        calls = []
        sidebar = docking.icon_sidebar
        original = sidebar.update_button_icon

        def spy(panel_id, icon_name="", color=""):
            calls.append((panel_id, icon_name, color))
            original(panel_id, icon_name, color)

        monkeypatch.setattr(sidebar, "update_button_icon", spy)
        return calls

    def test_success_uses_theme_success_color(self, docking, monkeypatch):
        _register_deferred(docking, "test.p1")
        calls = self._button_color_calls(docking, monkeypatch)
        docking._instantiate_deferred_panel("test.p1")
        success = ThemeManager.get_instance().colors.success
        assert ("test.p1", "", success) in calls

    def test_explicit_icon_color_overrides_status(self, docking, monkeypatch):
        _register_deferred(docking, "test.p1")
        panel = docking._instantiate_deferred_panel("test.p1")
        calls = self._button_color_calls(docking, monkeypatch)
        panel.set_sidebar_icon(color="#123456")
        panel.set_status(PanelStatus.ERROR)
        # Both refreshes use the explicit override, not the error color
        assert calls[-1] == ("test.p1", "", "#123456")

    def test_clearing_override_returns_to_status_color(
        self, docking, monkeypatch
    ):
        _register_deferred(docking, "test.p1")
        panel = docking._instantiate_deferred_panel("test.p1")
        panel.set_sidebar_icon(color="#123456")
        calls = self._button_color_calls(docking, monkeypatch)
        panel.set_sidebar_icon(color="")  # clear override
        success = ThemeManager.get_instance().colors.success
        assert calls[-1] == ("test.p1", "", success)

    def test_theme_change_refreshes_tracked_icons(self, docking, monkeypatch):
        _register_deferred(docking, "test.p1")
        docking._instantiate_deferred_panel("test.p1")
        calls = self._button_color_calls(docking, monkeypatch)
        ThemeManager.get_instance().colors_changed.emit()
        assert any(c[0] == "test.p1" for c in calls)


def _foreign_status_enum():
    """A second PanelStatus class, as importlib.reload of panels.base makes.

    Re-executing the module creates a new Enum class with the same names and
    values; its members are neither ``is`` nor ``==`` to the originals, so the
    DockingManager (which imported the first class) cannot match them.
    """
    from enum import Enum

    return Enum("PanelStatus", [(m.name, m.value) for m in PanelStatus])


class TestForeignStatusEnum:
    """LIGHTFALL-M: statuses from a reloaded copy of PanelStatus."""

    def test_theme_change_with_foreign_uninitialized_does_not_raise(self, docking, monkeypatch):
        foreign = _foreign_status_enum()
        _register_deferred(docking, "test.p1")
        docking._instantiate_deferred_panel("test.p1")
        docking.set_panel_status("test.p1", foreign.UNINITIALIZED)
        calls = TestIconTinting()._button_color_calls(docking, monkeypatch)
        ThemeManager.get_instance().colors_changed.emit()
        assert ("test.p1", "", "") in calls
        assert docking.get_panel_status("test.p1") is PanelStatus.UNINITIALIZED

    def test_foreign_status_maps_to_its_theme_color(self, docking, monkeypatch):
        foreign = _foreign_status_enum()
        _register_deferred(docking, "test.p1")
        docking._instantiate_deferred_panel("test.p1")
        calls = TestIconTinting()._button_color_calls(docking, monkeypatch)
        docking.set_panel_status("test.p1", foreign.WARNING)
        warning = ThemeManager.get_instance().colors.warning
        assert calls[-1] == ("test.p1", "", warning)
        assert docking.get_panel_status("test.p1") is PanelStatus.WARNING

    def test_panel_built_after_base_reload_marks_success(self, docking, monkeypatch):
        # After a reload, panels.base's global PanelStatus is the new class,
        # so BasePanel.__init__ starts the panel at the new UNINITIALIZED.
        foreign = _foreign_status_enum()
        monkeypatch.setattr("lightfall.ui.panels.base.PanelStatus", foreign)
        _register_deferred(docking, "test.p1")
        panel = docking._instantiate_deferred_panel("test.p1")
        assert panel.status is foreign.UNINITIALIZED
        assert docking.get_panel_status("test.p1") is PanelStatus.SUCCESS
        ThemeManager.get_instance().colors_changed.emit()  # must not raise
