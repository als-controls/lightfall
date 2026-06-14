"""Tests for scan geometry parsing."""
from __future__ import annotations

from lightfall.visualization.scan_geometry import parse_scan_geometry


class _FakeRun:
    def __init__(self, start: dict):
        self.metadata = {"start": start}


def test_rectilinear_2d_grid():
    run = _FakeRun({
        "hints": {"dimensions": [[["motor_y"], "primary"], [["motor_x"], "primary"]]},
        "shape": [5, 4],
    })
    geo = parse_scan_geometry(run)
    assert geo.motors == ["motor_y", "motor_x"]
    assert geo.n_dims == 2
    assert geo.is_rectilinear is True
    assert geo.grid_shape == (5, 4)


def test_non_rectilinear_2d_has_no_grid():
    run = _FakeRun({
        "hints": {"dimensions": [[["mx"], "primary"], [["my"], "primary"]]},
        # no "shape" key -> not rectilinear
    })
    geo = parse_scan_geometry(run)
    assert geo.motors == ["mx", "my"]
    assert geo.n_dims == 2
    assert geo.is_rectilinear is False
    assert geo.grid_shape == ()


def test_one_dimensional_scan():
    run = _FakeRun({
        "hints": {"dimensions": [[["theta"], "primary"]]},
        "shape": [10],
    })
    geo = parse_scan_geometry(run)
    assert geo.motors == ["theta"]
    assert geo.n_dims == 1
    assert geo.is_rectilinear is True
    assert geo.grid_shape == (10,)


def test_missing_metadata_is_safe():
    geo = parse_scan_geometry(_FakeRun({}))
    assert geo.motors == []
    assert geo.n_dims == 0
    assert geo.is_rectilinear is False
    assert geo.grid_shape == ()
