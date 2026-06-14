"""Tests for visualization reduction operators."""
from __future__ import annotations

import numpy as np
import pytest

from lightfall.visualization.reductions import (
    REDUCTIONS_BY_NAME,
    list_operators,
    operators_for_frame_count,
)


def _cube():
    # 3 frames, 2x2 pixels, deliberately simple
    return np.array(
        [
            [[1.0, 2.0], [3.0, 4.0]],   # frame 0, mean 2.5, sum 10
            [[2.0, 3.0], [4.0, 5.0]],   # frame 1, sum 14
            [[4.0, 5.0], [6.0, 7.0]],   # frame 2, sum 22
        ],
        dtype=np.float64,
    )


def test_mean_point_scalar_is_grand_mean():
    op = REDUCTIONS_BY_NAME["Mean"]
    assert op.point_scalar(_cube()) == pytest.approx(3.8333333, rel=1e-6)


def test_mean_per_frame_is_spatial_mean_per_frame():
    op = REDUCTIONS_BY_NAME["Mean"]
    series = op.per_frame(_cube())
    np.testing.assert_allclose(series, [2.5, 3.5, 5.5])


def test_sum_point_scalar_is_total():
    op = REDUCTIONS_BY_NAME["Sum"]
    assert op.point_scalar(_cube()) == pytest.approx(46.0)


def test_max_min_point_scalars():
    assert REDUCTIONS_BY_NAME["Max"].point_scalar(_cube()) == pytest.approx(7.0)
    assert REDUCTIONS_BY_NAME["Min"].point_scalar(_cube()) == pytest.approx(1.0)


def test_chi2_consecutive_point_scalar():
    # diffs frame1-0 == 1 everywhere; frame2-1 == {2,2,2,2}; squared means: 1 and 4
    # pixel-averaged then transition-averaged => mean([1, 4]) == 2.5
    op = REDUCTIONS_BY_NAME["Chi2 (consecutive)"]
    assert op.point_scalar(_cube()) == pytest.approx(2.5)


def test_chi2_consecutive_per_frame_is_nan_padded():
    op = REDUCTIONS_BY_NAME["Chi2 (consecutive)"]
    series = op.per_frame(_cube())
    assert np.isnan(series[0])
    np.testing.assert_allclose(series[1:], [1.0, 4.0])


def test_framewise_std_has_no_per_frame_series():
    op = REDUCTIONS_BY_NAME["Frame-wise Std (pixel-avg)"]
    assert op.per_frame is None
    # each pixel varies the same way across the 3 frames; std is identical per pixel
    expected = float(np.std(_cube(), axis=0).mean())
    assert op.point_scalar(_cube()) == pytest.approx(expected)


def test_operators_for_frame_count_filters_by_min_frames():
    single = {op.name for op in operators_for_frame_count(1)}
    assert "Mean" in single
    assert "Chi2 (consecutive)" not in single  # needs >= 2 frames
    multi = {op.name for op in operators_for_frame_count(5)}
    assert "Norm Abs Derivative" in multi  # needs >= 3


def test_list_operators_nonempty_and_named():
    ops = list_operators()
    assert ops, "registry must not be empty"
    assert all(op.display_name for op in ops)
