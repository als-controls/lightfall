"""Tests for exporter converters."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from lucid.exporter.converters.base import Converter


class TestConverterABC:
    def test_cannot_instantiate_base(self):
        with pytest.raises(TypeError):
            Converter()

    def test_subclass_must_implement_export(self):
        class Incomplete(Converter):
            name = "incomplete"

        with pytest.raises(TypeError):
            Incomplete()


class TestNoOpConverter:
    def _make_mock_run(self, fields: dict[str, np.ndarray]) -> MagicMock:
        """Create a mock Tiled run client with the given fields in primary stream."""
        run = MagicMock()
        stream = MagicMock()
        stream.keys.return_value = list(fields.keys())

        def _get_field(_self, key):
            col = MagicMock()
            col.read.return_value = fields[key]
            return col

        stream.__getitem__ = _get_field
        run.__getitem__ = lambda _self, _key: stream
        return run

    async def test_export_creates_output_files(self, tmp_path):
        from lucid.exporter.converters.noop import NoOpConverter

        converter = NoOpConverter()
        assert converter.name == "noop"

        data = {"detector": np.ones((5, 10, 10)), "motor": np.arange(5, dtype=float)}
        run = self._make_mock_run(data)

        await converter.export(
            run_client=run,
            run_uid="test-uid-001",
            params={},
            output_dir=tmp_path,
        )

        out_dir = tmp_path / "test-uid-001"
        assert out_dir.is_dir()
        assert (out_dir / "detector.npy").exists()
        assert (out_dir / "motor.npy").exists()

        loaded = np.load(out_dir / "detector.npy")
        np.testing.assert_array_equal(loaded, data["detector"])

    async def test_export_calls_progress_cb(self, tmp_path):
        from lucid.exporter.converters.noop import NoOpConverter

        converter = NoOpConverter()
        data = {"field1": np.array([1, 2, 3])}
        run = self._make_mock_run(data)
        cb = MagicMock()

        await converter.export(
            run_client=run,
            run_uid="uid-002",
            params={},
            output_dir=tmp_path,
            progress_cb=cb,
        )

        cb.assert_called()


def test_noop_registered_in_converter_registry():
    from lucid.exporter.converters import get_converter
    from lucid.exporter.converters.noop import NoOpConverter
    assert get_converter("noop") is NoOpConverter


import h5py  # noqa: E402


class TestNxsasConverter:
    def _make_mock_run(self, image_data: np.ndarray) -> MagicMock:
        """Create a mock Tiled run with an image field in primary stream."""
        run = MagicMock()
        stream = MagicMock()
        col = MagicMock()
        col.read.return_value = image_data
        stream.keys.return_value = ["detector"]
        stream.__getitem__ = lambda _self, key: col
        run.keys.return_value = ["primary"]
        run.__getitem__ = lambda _self, key: stream
        run.metadata = {
            "start": {
                "uid": "nxsas-test-uid",
                "plan_name": "count",
                "time": 1700000000.0,
            }
        }
        return run

    async def test_export_creates_hdf5(self, tmp_path):
        from lucid.exporter.converters.nxsas import NxsasConverter

        converter = NxsasConverter()
        assert converter.name == "nxsas"

        image_data = np.random.randint(0, 1000, (3, 100, 100), dtype=np.int32)
        run = self._make_mock_run(image_data)

        await converter.export(
            run_client=run,
            run_uid="nxsas-test-uid",
            params={"roi": {"x": 10, "y": 20, "width": 50, "height": 40}},
            output_dir=tmp_path,
        )

        out_file = tmp_path / "nxsas-test-uid.h5"
        assert out_file.exists()

        with h5py.File(out_file, "r") as f:
            assert "entry" in f
            assert "entry/data" in f
            assert "entry/data/data" in f
            data = f["entry/data/data"][:]
            # ROI: y=20..60, x=10..60 → shape (3, 40, 50)
            assert data.shape == (3, 40, 50)
            np.testing.assert_array_equal(data, image_data[:, 20:60, 10:60])

    async def test_export_without_roi_uses_full_frame(self, tmp_path):
        from lucid.exporter.converters.nxsas import NxsasConverter

        converter = NxsasConverter()
        image_data = np.ones((2, 50, 50), dtype=np.float32)
        run = self._make_mock_run(image_data)

        await converter.export(
            run_client=run,
            run_uid="nxsas-full-uid",
            params={},
            output_dir=tmp_path,
        )

        out_file = tmp_path / "nxsas-full-uid.h5"
        assert out_file.exists()

        with h5py.File(out_file, "r") as f:
            data = f["entry/data/data"][:]
            assert data.shape == (2, 50, 50)
