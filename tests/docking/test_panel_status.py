"""Tests for PanelStatus and BasePanel status API."""

from lightfall.ui.panels.base import BasePanel, PanelMetadata, PanelStatus


class _StatusPanel(BasePanel):
    panel_metadata = PanelMetadata(
        id="test.panels.status",
        name="Status Panel",
        icon="mdi6.alpha-s",
    )


class TestPanelStatusEnum:
    def test_members(self):
        assert {s.name for s in PanelStatus} == {
            "UNINITIALIZED", "SUCCESS", "WARNING", "ERROR", "INFO",
        }


class TestBasePanelStatus:
    def test_default_status_is_uninitialized(self, qtbot):
        panel = _StatusPanel()
        qtbot.addWidget(panel)
        assert panel.status is PanelStatus.UNINITIALIZED

    def test_set_status_stores_and_emits(self, qtbot):
        panel = _StatusPanel()
        qtbot.addWidget(panel)
        with qtbot.waitSignal(panel.status_changed) as blocker:
            panel.set_status(PanelStatus.WARNING)
        assert blocker.args == [PanelStatus.WARNING]
        assert panel.status is PanelStatus.WARNING

    def test_set_same_status_does_not_emit(self, qtbot):
        panel = _StatusPanel()
        qtbot.addWidget(panel)
        panel.set_status(PanelStatus.INFO)
        with qtbot.assertNotEmitted(panel.status_changed):
            panel.set_status(PanelStatus.INFO)


class TestPanelMetadataProactiveInit:
    def test_default_true(self):
        meta = PanelMetadata(id="x", name="X")
        assert meta.proactive_init is True

    def test_opt_out(self):
        meta = PanelMetadata(id="x", name="X", proactive_init=False)
        assert meta.proactive_init is False
