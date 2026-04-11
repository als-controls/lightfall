"""Tests for exporter converters."""

from __future__ import annotations

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


import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np


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

    def test_export_creates_output_files(self, tmp_path):
        from lucid.exporter.converters.noop import NoOpConverter

        converter = NoOpConverter()
        assert converter.name == "noop"

        data = {"detector": np.ones((5, 10, 10)), "motor": np.arange(5, dtype=float)}
        run = self._make_mock_run(data)

        asyncio.run(converter.export(
            run_client=run,
            run_uid="test-uid-001",
            params={},
            output_dir=tmp_path,
        ))

        out_dir = tmp_path / "test-uid-001"
        assert out_dir.is_dir()
        assert (out_dir / "detector.npy").exists()
        assert (out_dir / "motor.npy").exists()

        loaded = np.load(out_dir / "detector.npy")
        np.testing.assert_array_equal(loaded, data["detector"])

    def test_export_calls_progress_cb(self, tmp_path):
        from lucid.exporter.converters.noop import NoOpConverter

        converter = NoOpConverter()
        data = {"field1": np.array([1, 2, 3])}
        run = self._make_mock_run(data)
        cb = MagicMock()

        asyncio.run(converter.export(
            run_client=run,
            run_uid="uid-002",
            params={},
            output_dir=tmp_path,
            progress_cb=cb,
        ))

        cb.assert_called()
