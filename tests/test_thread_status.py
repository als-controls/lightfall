"""Tests for ThreadStatusPlugin and _ProgressOverlay extensions.

Tests device-level progress, scan-level progress, and status bar label updates.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from lucid.ui.statusbar.plugins.thread_status import (
    ThreadStatusPlugin,
    _ProgressOverlay,
)


@pytest.fixture
def qapp():
    """Ensure QApplication exists for Qt."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def process_events_for(qapp, duration_ms: int = 200) -> None:
    """Process Qt events for the given duration."""
    deadline = time.monotonic() + duration_ms / 1000
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


# ======================================================================
# _ProgressOverlay device row tests
# ======================================================================


class TestOverlayDeviceRows:
    """Tests for device progress rows in _ProgressOverlay."""

    def test_upsert_device_creates_row(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_device("motor1", 5.0, 0.0, 10.0, 0.5)

        assert "motor1" in overlay._device_rows
        assert "motor1" in overlay._device_bars
        assert "motor1" in overlay._device_labels
        assert overlay.device_count == 1

    def test_upsert_device_determinate(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_device("motor1", 5.0, 0.0, 10.0, 0.5)

        bar = overlay._device_bars["motor1"]
        assert bar.minimum() == 0
        assert bar.maximum() == 100
        assert bar.value() == 50

    def test_upsert_device_indeterminate(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_device("motor1", 0.0, 0.0, 0.0, -1.0)

        bar = overlay._device_bars["motor1"]
        assert bar.minimum() == 0
        assert bar.maximum() == 0  # pulsing

    def test_upsert_device_updates_existing(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_device("motor1", 0.0, 0.0, 10.0, 0.0)
        overlay.upsert_device("motor1", 5.0, 0.0, 10.0, 0.5)

        assert overlay.device_count == 1
        assert overlay._device_bars["motor1"].value() == 50

    def test_upsert_device_multiple(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_device("motor1", 5.0, 0.0, 10.0, 0.5)
        overlay.upsert_device("motor2", 3.0, 0.0, 10.0, 0.3)

        assert overlay.device_count == 2

    def test_mark_device_done(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_device("motor1", 5.0, 0.0, 10.0, 0.5)
        overlay.mark_device_done("motor1")

        bar = overlay._device_bars["motor1"]
        assert bar.maximum() == 100
        assert bar.value() == 100

    def test_mark_device_done_schedules_removal(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_device("motor1", 5.0, 0.0, 10.0, 0.5)
        overlay.mark_device_done("motor1")

        # Device still present immediately
        assert overlay.device_count == 1

        # After timer fires, device should be removed
        process_events_for(qapp, 1200)
        assert overlay.device_count == 0

    def test_mark_device_done_unknown_device(self, qapp):
        """mark_device_done on unknown device should not raise."""
        overlay = _ProgressOverlay()
        overlay.mark_device_done("nonexistent")  # Should be a no-op

    def test_clear_devices_removes_all(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_device("motor1", 5.0, 0.0, 10.0, 0.5)
        overlay.upsert_device("motor2", 3.0, 0.0, 10.0, 0.3)

        overlay.clear_devices()

        assert overlay.device_count == 0
        assert len(overlay._device_removal_timers) == 0

    def test_clear_devices_cancels_pending_timers(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_device("motor1", 5.0, 0.0, 10.0, 0.5)
        overlay.mark_device_done("motor1")

        # Timer is pending
        assert len(overlay._device_removal_timers) == 1

        overlay.clear_devices()

        # All cleared
        assert overlay.device_count == 0
        assert len(overlay._device_removal_timers) == 0


# ======================================================================
# _ProgressOverlay scan row tests
# ======================================================================


class TestOverlayScanRow:
    """Tests for scan progress row in _ProgressOverlay."""

    def test_upsert_scan_creates_row(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_scan(0, 10)

        assert overlay._scan_row is not None
        assert overlay._scan_bar is not None
        assert overlay._scan_label is not None
        assert overlay.has_scan is True

    def test_upsert_scan_determinate(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_scan(5, 10)

        assert overlay._scan_bar.minimum() == 0
        assert overlay._scan_bar.maximum() == 100
        assert overlay._scan_bar.value() == 50
        assert "5/10" in overlay._scan_label.text()

    def test_upsert_scan_indeterminate(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_scan(3, None)

        assert overlay._scan_bar.maximum() == 0  # pulsing
        assert "3 pts" in overlay._scan_label.text()

    def test_upsert_scan_updates_existing(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_scan(0, 10)
        overlay.upsert_scan(5, 10)

        assert overlay._scan_bar.value() == 50

    def test_mark_scan_done(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_scan(10, 10)
        overlay.mark_scan_done()

        assert overlay._scan_bar.value() == 100
        assert "complete" in overlay._scan_label.text()

    def test_mark_scan_done_schedules_removal(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_scan(10, 10)
        overlay.mark_scan_done()

        assert overlay.has_scan is True

        process_events_for(qapp, 1200)
        assert overlay.has_scan is False

    def test_mark_scan_done_when_no_scan(self, qapp):
        """mark_scan_done when no scan row should not raise."""
        overlay = _ProgressOverlay()
        overlay.mark_scan_done()  # no-op


# ======================================================================
# _ProgressOverlay separator tests
# ======================================================================


class TestOverlaySeparator:
    """Tests for separator line between thread and device sections."""

    def test_no_separator_threads_only(self, qapp):
        overlay = _ProgressOverlay()
        thread = MagicMock()
        thread._name = "test"
        overlay.upsert(thread, 50, 0, 100)

        assert overlay._separator is None

    def test_no_separator_devices_only(self, qapp):
        overlay = _ProgressOverlay()
        overlay.upsert_device("motor1", 5.0, 0.0, 10.0, 0.5)

        assert overlay._separator is None

    def test_separator_when_both(self, qapp):
        overlay = _ProgressOverlay()
        thread = MagicMock()
        thread._name = "test"
        overlay.upsert(thread, 50, 0, 100)
        overlay.upsert_device("motor1", 5.0, 0.0, 10.0, 0.5)

        assert overlay._separator is not None

    def test_separator_removed_when_devices_cleared(self, qapp):
        overlay = _ProgressOverlay()
        thread = MagicMock()
        thread._name = "test"
        overlay.upsert(thread, 50, 0, 100)
        overlay.upsert_device("motor1", 5.0, 0.0, 10.0, 0.5)
        assert overlay._separator is not None

        overlay.clear_devices()
        assert overlay._separator is None


# ======================================================================
# ThreadStatusPlugin label text tests
# ======================================================================


class TestPluginLabelText:
    """Tests for status bar label text in different states."""

    def _make_plugin(self):
        plugin = ThreadStatusPlugin()
        # Keep a reference to the container so Qt doesn't GC the C++ objects
        plugin._container = plugin.create_widget()
        return plugin

    def test_hidden_when_idle(self, qapp):
        plugin = self._make_plugin()
        plugin.update()
        assert plugin._button.text() == ""

    def test_scanning_no_tasks(self, qapp):
        plugin = self._make_plugin()
        plugin._scanning = True
        plugin.update()
        assert "scanning" in plugin._button.text()

    def test_scanning_with_tasks(self, qapp):
        plugin = self._make_plugin()
        plugin._scanning = True
        plugin._tracked.add(1)
        plugin._tracked.add(2)
        plugin.update()
        assert "scan" in plugin._button.text()
        assert "2 tasks" in plugin._button.text()

    def test_no_scan_with_tasks(self, qapp):
        plugin = self._make_plugin()
        plugin._tracked.add(1)
        plugin.update()
        assert "1 task" in plugin._button.text()
        # Should not mention scan
        assert "scan" not in plugin._button.text()


# ======================================================================
# ThreadStatusPlugin document callback tests
# ======================================================================


class TestPluginDocumentCallback:
    """Tests for _on_document handling scan progress."""

    def _make_plugin(self):
        plugin = ThreadStatusPlugin()
        plugin._container = plugin.create_widget()
        return plugin

    def test_start_document_begins_scan(self, qapp):
        plugin = self._make_plugin()
        doc = {"uid": "abc-123", "num_points": 10}
        plugin._on_document("start", doc)

        assert plugin._scanning is True
        assert plugin._scan_uid == "abc-123"
        assert plugin._scan_num_points == 10
        assert plugin._overlay.has_scan is True

    def test_start_document_no_num_points(self, qapp):
        plugin = self._make_plugin()
        doc = {"uid": "abc-123"}
        plugin._on_document("start", doc)

        assert plugin._scanning is True
        assert plugin._scan_num_points is None

    def test_event_document_increments(self, qapp):
        plugin = self._make_plugin()
        plugin._on_document("start", {"uid": "abc-123", "num_points": 10})
        plugin._on_document("event", {"uid": "abc-123", "seq_num": 1})
        plugin._on_document("event", {"uid": "abc-123", "seq_num": 2})

        assert plugin._scan_event_count == 2

    def test_stop_document_ends_scan(self, qapp):
        plugin = self._make_plugin()
        plugin._on_document("start", {"uid": "abc-123", "num_points": 5})
        for i in range(5):
            plugin._on_document("event", {"seq_num": i + 1})
        plugin._on_document("stop", {"uid": "abc-123"})

        assert plugin._scanning is False
        assert plugin._scan_uid is None
        assert plugin._scan_event_count == 0

    def test_new_scan_cancels_pending_removal(self, qapp):
        """If Scan A stops and Scan B starts within 1s, the removal timer
        should be cancelled so Scan B's row is not destroyed."""
        plugin = self._make_plugin()
        # Scan A
        plugin._on_document("start", {"uid": "scan-a", "num_points": 5})
        plugin._on_document("stop", {"uid": "scan-a"})
        # Removal timer is now pending
        assert plugin._overlay._scan_removal_timer is not None

        # Scan B starts before the timer fires
        plugin._on_document("start", {"uid": "scan-b", "num_points": 10})
        # Timer should have been cancelled
        assert plugin._overlay._scan_removal_timer is None
        # Scan B row should be present
        assert plugin._overlay.has_scan is True
        assert plugin._scan_uid == "scan-b"

        # Wait past the original timer — row should still exist
        process_events_for(qapp, 1200)
        assert plugin._overlay.has_scan is True


# ======================================================================
# ThreadStatusPlugin device callbacks tests
# ======================================================================


class TestPluginDeviceCallbacks:
    """Tests for device progress callbacks in the plugin."""

    def _make_plugin(self):
        plugin = ThreadStatusPlugin()
        plugin._container = plugin.create_widget()
        return plugin

    def test_on_device_progress(self, qapp):
        plugin = self._make_plugin()
        plugin._on_device_progress("motor1", 5.0, 0.0, 10.0, 0.5)

        assert plugin._overlay.device_count == 1

    def test_on_device_finished(self, qapp):
        plugin = self._make_plugin()
        plugin._on_device_progress("motor1", 5.0, 0.0, 10.0, 0.5)
        plugin._on_device_finished("motor1")

        bar = plugin._overlay._device_bars["motor1"]
        assert bar.value() == 100

    def test_on_wait_cleared(self, qapp):
        plugin = self._make_plugin()
        plugin._on_device_progress("motor1", 5.0, 0.0, 10.0, 0.5)
        plugin._on_device_progress("motor2", 3.0, 0.0, 10.0, 0.3)
        plugin._on_wait_cleared()

        assert plugin._overlay.device_count == 0


# ======================================================================
# ThreadStatusPlugin connect/disconnect tests
# ======================================================================


class TestPluginSignalConnection:
    """Tests for connect_signals and disconnect_signals."""

    def test_connect_signals_without_engine(self, qapp):
        """connect_signals should not raise if engine is unavailable."""
        plugin = ThreadStatusPlugin()
        plugin._container = plugin.create_widget()
        # Patch get_engine to raise
        with patch(
            "lucid.acquire.get_engine",
            side_effect=RuntimeError("no engine"),
        ):
            # Should not raise — falls back gracefully
            plugin.connect_signals()

    def test_disconnect_signals_without_engine(self, qapp):
        """disconnect_signals should not raise if engine is unavailable."""
        plugin = ThreadStatusPlugin()
        plugin._container = plugin.create_widget()
        with patch(
            "lucid.acquire.get_engine",
            side_effect=RuntimeError("no engine"),
        ):
            plugin.disconnect_signals()

    def test_connect_signals_with_engine_no_bridge(self, qapp):
        """connect_signals with an engine that has no waiting_bridge."""
        plugin = ThreadStatusPlugin()
        plugin._container = plugin.create_widget()
        mock_engine = MagicMock()
        del mock_engine.waiting_bridge  # hasattr will return False
        with patch(
            "lucid.acquire.get_engine",
            return_value=mock_engine,
        ):
            plugin.connect_signals()
            # sigOutput should still be connected
            mock_engine.sigOutput.connect.assert_called_once()


# ======================================================================
# Plan name in status text
# ======================================================================


class TestPlanNameInStatus:
    """Tests for plan-name rendering in the status-bar button text."""

    def _make_plugin(self):
        plugin = ThreadStatusPlugin()
        plugin._container = plugin.create_widget()
        return plugin

    def test_start_doc_records_plan_name(self, qapp):
        plugin = self._make_plugin()
        plugin._on_document("start", {"uid": "u", "plan_name": "scan", "num_points": 3})
        assert plugin._scan_plan_name == "scan"

    def test_start_doc_without_plan_name_leaves_none(self, qapp):
        plugin = self._make_plugin()
        plugin._on_document("start", {"uid": "u"})
        assert plugin._scan_plan_name is None

    def test_stop_doc_clears_plan_name(self, qapp):
        plugin = self._make_plugin()
        plugin._on_document("start", {"uid": "u", "plan_name": "scan"})
        plugin._on_document("stop", {"uid": "u"})
        assert plugin._scan_plan_name is None

    def test_status_text_uses_plan_name_when_known(self, qapp):
        plugin = self._make_plugin()
        plugin._on_document("start", {"uid": "u", "plan_name": "grid_scan"})
        # _on_document calls update() during start
        assert "grid_scan" in plugin._button.text()
        assert "Plan 'grid_scan' running" in plugin._button.toolTip()

    def test_status_text_falls_back_when_plan_name_missing(self, qapp):
        plugin = self._make_plugin()
        plugin._on_document("start", {"uid": "u"})
        assert "scanning" in plugin._button.text()

    def test_status_text_with_plan_and_tasks(self, qapp):
        plugin = self._make_plugin()
        plugin._on_document("start", {"uid": "u", "plan_name": "rel_scan"})
        plugin._tracked.add(1)
        plugin._tracked.add(2)
        plugin.update()
        assert "rel_scan + 2 tasks" in plugin._button.text()
