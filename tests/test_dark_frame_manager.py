"""Tests for DarkFrameManager."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from lightfall.ui.widgets.camera.dark_frames import DarkFrameManager


class TestDarkFrameManager:

    def test_initial_state(self):
        mgr = DarkFrameManager(device_name="sim_det")
        assert mgr.dark_frame is None
        assert mgr.has_dark is False

    def test_handles_dark_stream_with_embedded_data(self):
        """Embedded arrays are cached immediately on event."""
        mgr = DarkFrameManager(device_name="sim_det")
        mgr("start", {"uid": "run-123", "time": 0})
        mgr("descriptor", {
            "uid": "desc-1", "name": "dark",
            "data_keys": {"sim_det_image": {"shape": [480, 640]}},
        })
        mgr("event", {
            "descriptor": "desc-1",
            "data": {"sim_det_image": np.zeros((480, 640))},
            "filled": {"sim_det_image": True},
            "seq_num": 1, "time": 0,
        })
        # Dark cached immediately, no need to wait for stop
        assert mgr.has_dark is True
        assert mgr.dark_frame.shape == (480, 640)

    def test_handles_dark_stream_with_datum_reference(self):
        """Datum refs trigger Tiled readback."""
        dark_data = np.full((480, 640), 42.0)
        mock_tiled = MagicMock()
        mock_tiled.is_connected = True
        mock_xarray = MagicMock()
        mock_xarray.values = dark_data[np.newaxis, ...]
        mock_run = MagicMock()
        mock_run.__getitem__ = MagicMock(side_effect=lambda k: {
            "dark": MagicMock(__getitem__=MagicMock(side_effect=lambda k2: {
                "data": MagicMock(__getitem__=MagicMock(side_effect=lambda k3: {
                    "sim_det_image": mock_xarray,
                }[k3]))
            }[k2]))
        }[k])
        mock_tiled._client = MagicMock()
        mock_tiled._client.__getitem__ = MagicMock(return_value=mock_run)

        with patch(
            "lightfall.services.tiled_service.TiledService.get_instance",
            return_value=mock_tiled,
        ):
            mgr = DarkFrameManager(device_name="sim_det")
            mgr("start", {"uid": "run-123", "time": 0})
            mgr("descriptor", {
                "uid": "desc-1", "name": "dark",
                "data_keys": {"sim_det_image": {"shape": [480, 640]}},
            })
            mgr("event", {
                "descriptor": "desc-1",
                "data": {"sim_det_image": "datum-uid-abc"},
                "filled": {},
                "seq_num": 1, "time": 0,
            })
        assert mgr.has_dark is True
        np.testing.assert_allclose(mgr.dark_frame, 42.0)

    def test_ignores_primary_stream(self):
        mgr = DarkFrameManager(device_name="sim_det")
        mgr("start", {"uid": "run-123", "time": 0})
        mgr("descriptor", {
            "uid": "desc-1", "name": "primary",
            "data_keys": {"sim_det_image": {"shape": [480, 640]}},
        })
        mgr("event", {
            "descriptor": "desc-1",
            "data": {"sim_det_image": np.ones((480, 640))},
            "filled": {"sim_det_image": True},
            "seq_num": 1, "time": 0,
        })
        mgr("stop", {"uid": "run-123", "exit_status": "success"})
        assert mgr.has_dark is False

    def test_caches_multiple_embedded_dark_frames(self):
        mgr = DarkFrameManager(device_name="sim_det")
        mgr("start", {"uid": "run-123", "time": 0})
        mgr("descriptor", {
            "uid": "desc-1", "name": "dark",
            "data_keys": {"sim_det_image": {"shape": [10, 10]}},
        })
        frame1 = np.full((10, 10), 100.0)
        frame2 = np.full((10, 10), 200.0)
        mgr("event", {"descriptor": "desc-1", "data": {"sim_det_image": frame1}, "filled": {"sim_det_image": True}, "seq_num": 1, "time": 0})
        mgr("event", {"descriptor": "desc-1", "data": {"sim_det_image": frame2}, "filled": {"sim_det_image": True}, "seq_num": 2, "time": 0})
        assert mgr.has_dark
        np.testing.assert_allclose(mgr.dark_frame, 150.0)

    def test_clear_dark(self):
        mgr = DarkFrameManager(device_name="sim_det")
        mgr._cached_dark = np.zeros((10, 10))
        assert mgr.has_dark
        mgr.clear()
        assert not mgr.has_dark

    def test_subtract(self):
        mgr = DarkFrameManager(device_name="sim_det")
        mgr._cached_dark = np.full((10, 10), 50.0)
        result = mgr.subtract(np.full((10, 10), 200.0))
        np.testing.assert_allclose(result, 150.0)

    def test_subtract_clips_to_zero(self):
        mgr = DarkFrameManager(device_name="sim_det")
        mgr._cached_dark = np.full((10, 10), 200.0)
        result = mgr.subtract(np.full((10, 10), 50.0))
        assert np.all(result >= 0)

    def test_subtract_no_dark_returns_original(self):
        mgr = DarkFrameManager(device_name="sim_det")
        image = np.full((10, 10), 100.0)
        result = mgr.subtract(image)
        np.testing.assert_array_equal(result, image)


class TestLoadDarkFromTiled:

    def test_load_from_recent_run(self):
        dark_data = np.full((100, 100), 77.0)
        mock_tiled = MagicMock()
        mock_tiled.is_connected = True
        mock_client = MagicMock()
        mock_tiled._client = mock_client

        mock_xarray = MagicMock()
        mock_xarray.values = dark_data[np.newaxis, ...]
        mock_run = MagicMock()
        mock_run.keys.return_value = ["dark", "primary"]
        mock_dark_stream = MagicMock()
        mock_dark_data = MagicMock()
        mock_dark_data.__getitem__ = MagicMock(
            side_effect=lambda k: {"sim_det_image": mock_xarray}[k]
        )
        mock_dark_stream.__getitem__ = MagicMock(
            side_effect=lambda k: {"data": mock_dark_data}[k]
        )
        mock_run.__getitem__ = MagicMock(
            side_effect=lambda k: {"dark": mock_dark_stream}[k]
        )
        mock_client.values_indexer.__getitem__ = MagicMock(return_value=[mock_run])

        with patch(
            "lightfall.services.tiled_service.TiledService.get_instance",
            return_value=mock_tiled,
        ):
            mgr = DarkFrameManager(device_name="sim_det")
            mgr.load_dark_from_tiled(image_field="sim_det_image", search_last_n=5)
        assert mgr.has_dark
        np.testing.assert_allclose(mgr.dark_frame, 77.0)

    def test_no_tiled_connection_is_noop(self):
        mock_tiled = MagicMock()
        mock_tiled.is_connected = False
        with patch(
            "lightfall.services.tiled_service.TiledService.get_instance",
            return_value=mock_tiled,
        ):
            mgr = DarkFrameManager(device_name="sim_det")
            mgr.load_dark_from_tiled(image_field="sim_det_image")
        assert not mgr.has_dark
