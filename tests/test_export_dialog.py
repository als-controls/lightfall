"""Tests for export dialog parameter assembly."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lucid.ui.dialogs.export_dialog import build_job_message


class TestBuildJobMessage:
    def test_builds_noop_job(self):
        records = [MagicMock(uid="uid1"), MagicMock(uid="uid2")]
        msg = build_job_message(
            records=records,
            export_type="noop",
            output_dir="/tmp/export",
            tiled_url="https://tiled.example.com",
            auth_token="tok123",
            extra_params={},
        )
        assert msg["run_uids"] == ["uid1", "uid2"]
        assert msg["export_type"] == "noop"
        assert msg["params"]["output_dir"] == "/tmp/export"
        assert msg["tiled_url"] == "https://tiled.example.com"
        assert msg["auth_token"] == "tok123"
        assert "job_id" in msg

    def test_builds_nxsas_job_with_roi(self):
        records = [MagicMock(uid="uid1")]
        roi = {"x": 10, "y": 20, "width": 50, "height": 40}
        msg = build_job_message(
            records=records,
            export_type="nxsas",
            output_dir="/data/out",
            tiled_url="https://tiled.example.com",
            auth_token=None,
            extra_params={"roi": roi},
        )
        assert msg["export_type"] == "nxsas"
        assert msg["params"]["roi"] == roi


from unittest.mock import patch, MagicMock

from lucid.ui.dialogs.export_dialog import ping_or_spawn_exporter


class TestPingOrSpawnExporter:
    def test_ping_success_returns_true(self):
        """If exporter responds to ping, return True without spawning."""
        ipc = MagicMock()
        ipc.request.return_value = {"hostname": "test", "status": "ready"}

        result = ping_or_spawn_exporter(
            ipc=ipc,
            ping_subject="lucid.export.test.ping",
            nats_url="nats://localhost:4222",
        )
        assert result is True
        ipc.request.assert_called_once()

    @patch("lucid.ui.dialogs.export_dialog.subprocess.Popen")
    def test_ping_fail_spawns_then_retries(self, mock_popen):
        """If first ping fails, spawn exporter, retry pings."""
        ipc = MagicMock()
        # First ping fails, second succeeds (after spawn)
        ipc.request.side_effect = [None, {"hostname": "test", "status": "ready"}]

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        mock_popen.return_value = mock_proc

        result = ping_or_spawn_exporter(
            ipc=ipc,
            ping_subject="lucid.export.test.ping",
            nats_url="nats://localhost:4222",
        )
        assert result is True
        mock_popen.assert_called_once()
        assert ipc.request.call_count == 2

    @patch("lucid.ui.dialogs.export_dialog.subprocess.Popen")
    def test_all_pings_fail_returns_false(self, mock_popen):
        """If all pings fail after spawn, return False."""
        ipc = MagicMock()
        ipc.request.return_value = None  # all pings fail

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # process exited
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read.return_value = b"Connection refused"
        mock_popen.return_value = mock_proc

        result = ping_or_spawn_exporter(
            ipc=ipc,
            ping_subject="lucid.export.test.ping",
            nats_url="nats://localhost:4222",
        )
        assert result is False
