"""Tests for tiled slice-string helpers."""
from __future__ import annotations

from lightfall.utils.tiled_helpers import _build_slice_string, _subcube_shape


def test_build_slice_string_mixed():
    # point 3, all frames, rows 10:20, cols 5:8
    s = _build_slice_string((3, None, (10, 20), (5, 8)))
    assert s == "3,::,10:20,5:8"


def test_build_slice_string_single_frame():
    assert _build_slice_string((3, 7, None, None)) == "3,7,::,::"


def test_subcube_shape_drops_int_axes():
    full = (50, 100, 256, 256)  # (n_points, n_frames, H, W)
    # point 3, all frames, rows 10:20, cols 5:8 -> (n_frames, 10, 3)
    assert _subcube_shape((3, None, (10, 20), (5, 8)), full) == (100, 10, 3)


def test_subcube_shape_single_frame_is_2d():
    full = (50, 100, 256, 256)
    assert _subcube_shape((3, 7, None, None), full) == (256, 256)


def test_subcube_shape_3d_cube_point_roi():
    full = (40, 256, 256)  # (n_points, H, W), one frame per point
    assert _subcube_shape((3, (0, 16), (0, 8)), full) == (16, 8)
