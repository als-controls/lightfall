"""Tests for export dialog parameter assembly."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lightfall.ui.dialogs.export_dialog import build_job_message


class TestBuildJobMessage:
    def test_builds_noop_job(self):
        records = [MagicMock(uid="uid1"), MagicMock(uid="uid2")]
        msg = build_job_message(
            records=records,
            export_type="noop",
            output_dir="/tmp/export",
            tiled_url="https://tiled.example.com",
            tiled_api_key="apikey-secret",
            extra_params={},
        )
        assert msg["run_uids"] == ["uid1", "uid2"]
        assert msg["export_type"] == "noop"
        assert msg["params"]["output_dir"] == "/tmp/export"
        assert msg["tiled_url"] == "https://tiled.example.com"
        assert msg["tiled_api_key"] == "apikey-secret"
        assert "job_id" in msg

    def test_builds_nxsas_job_with_roi(self):
        records = [MagicMock(uid="uid1")]
        roi = {"x": 10, "y": 20, "width": 50, "height": 40}
        msg = build_job_message(
            records=records,
            export_type="nxsas",
            output_dir="/data/out",
            tiled_url="https://tiled.example.com",
            tiled_api_key=None,
            extra_params={"roi": roi},
        )
        assert msg["export_type"] == "nxsas"
        assert msg["params"]["roi"] == roi
        assert msg["tiled_api_key"] is None


from unittest.mock import patch, MagicMock

from lightfall.ui.dialogs.export_dialog import ping_or_spawn_exporter


class TestPingOrSpawnExporter:
    def test_ping_success_returns_true(self):
        """If exporter responds to ping, return True without spawning."""
        ipc = MagicMock()
        ipc.request.return_value = {"hostname": "test", "status": "ready"}

        result = ping_or_spawn_exporter(
            ipc=ipc,
            ping_subject="lightfall.export.test.ping",
            nats_url="nats://localhost:4222",
        )
        assert result is True
        ipc.request.assert_called_once()

    @patch("lightfall.ui.dialogs.export_dialog.subprocess.Popen")
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
            ping_subject="lightfall.export.test.ping",
            nats_url="nats://localhost:4222",
        )
        assert result is True
        mock_popen.assert_called_once()
        assert ipc.request.call_count == 2

    @patch("lightfall.ui.dialogs.export_dialog.subprocess.Popen")
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
            ping_subject="lightfall.export.test.ping",
            nats_url="nats://localhost:4222",
        )
        assert result is False


import numpy as np

from lightfall.ui.dialogs.export_dialog import load_sample_frame


class TestLoadSampleFrame:
    def _make_mock_client(self, image_data: np.ndarray | None = None, scalar_only: bool = False):
        """Create a mock Tiled client with a run containing image or scalar data."""
        client = MagicMock()
        run = MagicMock()
        stream = MagicMock()

        if scalar_only:
            data_keys = {"motor": {"shape": []}, "detector_stats": {"shape": [1]}}
            stream.metadata = {"data_keys": data_keys}
            stream.keys.return_value = ["motor", "detector_stats"]
        else:
            shape = list(image_data.shape) if image_data is not None else [10, 100, 100]
            data_keys = {"detector": {"shape": shape}}
            stream.metadata = {"data_keys": data_keys}
            stream.keys.return_value = ["detector"]
            col = MagicMock()
            col.shape = tuple(shape)
            if image_data is not None:
                col.read.return_value = image_data
            else:
                col.read.return_value = np.zeros((10, 100, 100))
            stream.__getitem__ = lambda _self, key: col

        run.keys.return_value = ["primary"]
        run.__getitem__ = lambda _self, key: stream
        run.metadata = {"start": {"uid": "test-uid"}}
        client.__getitem__ = lambda _self, key: run

        return client

    @patch("lightfall.utils.tiled_helpers.fetch_frame")
    def test_loads_middle_frame_from_3d(self, mock_fetch):
        image_data = np.arange(30).reshape(3, 2, 5).astype(np.float32)
        # fetch_frame is called for 3D data — mock it to return the middle frame
        mock_fetch.return_value = image_data[1]
        client = self._make_mock_client(image_data)

        frame = load_sample_frame(client, "run-key")
        assert frame.ndim == 2
        assert frame.shape == (2, 5)
        # Middle frame of 3 is index 1
        np.testing.assert_array_equal(frame, image_data[1])
        mock_fetch.assert_called_once()

    def test_loads_2d_directly(self):
        image_data = np.ones((50, 60), dtype=np.float32)
        client = self._make_mock_client(image_data)

        frame = load_sample_frame(client, "run-key")
        assert frame.shape == (50, 60)

    def test_raises_on_scalar_only(self):
        client = self._make_mock_client(scalar_only=True)

        with pytest.raises(ValueError, match="No image field"):
            load_sample_frame(client, "run-key")
