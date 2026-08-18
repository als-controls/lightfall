"""Acceptance test: mock area_det writes HDF5 through a real RunEngine.

Covers the Task 11 feasibility gate outcome: an ophyd-async sim detector
(``ophyd_async.sim.SimBlobDetector``) is wired into the mock roster,
connects through Lightfall's async-connect path via ``MockBackend.
check_connection``, and a ``count()`` plan writes real per-run HDF5 files
and emits ``stream_resource``/``stream_datum`` documents.
"""
from __future__ import annotations

import h5py
import pytest
from bluesky import RunEngine
from bluesky.plans import count

from lightfall.devices.backends.mock import MockBackend

pytest.importorskip("ophyd_async")


@pytest.fixture
def area_det(tmp_path, monkeypatch):
    monkeypatch.setenv("LIGHTFALL_SIM_AD_DIR", str(tmp_path))
    backend = MockBackend()
    infos = {i.name: i for i in backend.load_metadata()}
    assert "area_det" in infos, "mock roster must expose an area_det device"
    det = backend.instantiate(infos["area_det"])
    assert det is not None
    assert backend.check_connection(det, timeout=10.0)
    return det, tmp_path


def test_area_det_in_roster_metadata():
    backend = MockBackend()
    infos = {i.name: i for i in backend.load_metadata()}
    info = infos["area_det"]
    assert info.category.name == "DETECTOR"
    assert "camera" in info.tags
    assert "hdf5" in info.tags
    synoptic = info.metadata["synoptic"]
    assert synoptic["position"][0] == 27.0


def test_count_writes_hdf5(area_det):
    det, tmp_path = area_det
    RE = RunEngine()
    docs = []
    RE.subscribe(lambda name, doc: docs.append(name))
    RE(count([det], num=3))

    h5_files = list(tmp_path.rglob("*.h5"))
    assert h5_files

    with h5py.File(h5_files[0], "r") as f:
        def _descend(obj):
            while hasattr(obj, "values") and not hasattr(obj, "shape"):
                obj = next(iter(obj.values()))
            return obj

        dataset = _descend(f)
        assert dataset.shape[0] == 3

    assert "stream_resource" in docs
    assert "stream_datum" in docs
