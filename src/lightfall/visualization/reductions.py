"""Basic measurement-evaluation reductions for visualization.

Pure-numpy operators that reduce an image series — a per-scan-point sub-cube
of shape ``(n_frames, H, W)`` — to either a single scalar (``point_scalar``,
used by the Scan Viewer left map) or a per-frame series (``per_frame``, used
by the Image Stack over-frames curve).

These are deliberately *basic* reductions for evaluating a measurement at the
instrument, not a data-analysis package: numpy only, no q-space / azimuthal /
fitting operators. Inputs may already be ROI-cropped; operators never assume a
particular spatial extent.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

_EPS = 1e-12


@dataclass(frozen=True)
class ReductionOperator:
    """A named reduction.

    Attributes:
        name: Stable identifier (also the combo-box text).
        display_name: Human label (currently same as name).
        min_frames: Minimum frames in the sub-cube for this operator to apply.
        point_scalar: ``(n_frames, H, W) -> float`` collapse for the left map.
        per_frame: ``(n_frames, H, W) -> (n_frames,)`` NaN-padded series for the
            Image Stack curve. NaN for undefined frames (leading frame(s) for
            consecutive/vs-first operators; both leading and trailing for the
            central-difference derivative), or ``None`` if no meaningful per-frame
            value exists.
    """

    name: str
    display_name: str
    min_frames: int
    point_scalar: Callable[[np.ndarray], float]
    per_frame: Callable[[np.ndarray], np.ndarray] | None


def _frames_2d(cube: np.ndarray) -> np.ndarray:
    """Reshape ``(n, H, W)`` to ``(n, H*W)``."""
    return cube.reshape(cube.shape[0], -1)


def _nan_padded(n: int, start: int, values: np.ndarray) -> np.ndarray:
    """Length-``n`` array, NaN before ``start``, then ``values``."""
    out = np.full(n, np.nan, dtype=np.float64)
    out[start : start + len(values)] = values
    return out


# ---- per_frame builders (return length-n NaN-padded series) ----------------

def _pf_consecutive(metric: Callable[[np.ndarray, np.ndarray], np.ndarray]):
    def f(cube: np.ndarray) -> np.ndarray:
        n = cube.shape[0]
        if n < 2:
            return np.full(n, np.nan, dtype=np.float64)
        vals = metric(cube[1:], cube[:-1]).reshape(n - 1, -1).mean(axis=1)
        return _nan_padded(n, 1, vals)
    return f


def _sq_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    d = a - b
    return d * d


def _abs_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.abs(a - b)


def _norm_abs_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.abs(a - b) / (np.abs(a) + np.abs(b) + _EPS)


def _pf_vs_first(cube: np.ndarray) -> np.ndarray:
    n = cube.shape[0]
    if n < 2:
        return np.full(n, np.nan, dtype=np.float64)
    vals = _sq_diff(cube[1:], cube[0]).reshape(n - 1, -1).mean(axis=1)
    return _nan_padded(n, 1, vals)


def _pf_norm_abs_derivative(cube: np.ndarray) -> np.ndarray:
    n = cube.shape[0]
    if n < 3:
        return np.full(n, np.nan, dtype=np.float64)
    fwd = np.abs(cube[2:] - cube[1:-1])
    bwd = np.abs(cube[1:-1] - cube[:-2])
    den = 2.0 * np.abs(cube[1:-1]) + _EPS
    vals = ((fwd + bwd) / den).reshape(n - 2, -1).mean(axis=1)
    return _nan_padded(n, 1, vals)


# ---- point_scalar helpers --------------------------------------------------

def _scalar_from_per_frame(pf: Callable[[np.ndarray], np.ndarray]):
    return lambda cube: float(np.nanmean(pf(cube)))


REDUCTION_OPERATORS: list[ReductionOperator] = [
    ReductionOperator(
        "Mean", "Mean", 1,
        lambda c: float(np.nanmean(c)),
        lambda c: np.nanmean(_frames_2d(c), axis=1),
    ),
    ReductionOperator(
        "Min", "Min", 1,
        lambda c: float(np.nanmin(c)),
        lambda c: np.nanmin(_frames_2d(c), axis=1),
    ),
    ReductionOperator(
        "Max", "Max", 1,
        lambda c: float(np.nanmax(c)),
        lambda c: np.nanmax(_frames_2d(c), axis=1),
    ),
    ReductionOperator(
        "Sum", "Sum", 1,
        lambda c: float(np.nansum(c)),
        lambda c: np.nansum(_frames_2d(c), axis=1),
    ),
    ReductionOperator(
        "Frame-wise Std (pixel-avg)", "Frame-wise Std (pixel-avg)", 2,
        lambda c: float(np.nanmean(np.nanstd(c, axis=0))),
        None,
    ),
    ReductionOperator(
        "Chi2 (consecutive)", "Chi2 (consecutive)", 2,
        _scalar_from_per_frame(_pf_consecutive(_sq_diff)),
        _pf_consecutive(_sq_diff),
    ),
    ReductionOperator(
        "Chi2 (vs first)", "Chi2 (vs first)", 2,
        _scalar_from_per_frame(_pf_vs_first),
        _pf_vs_first,
    ),
    ReductionOperator(
        "Abs Diff", "Abs Diff", 2,
        _scalar_from_per_frame(_pf_consecutive(_abs_diff)),
        _pf_consecutive(_abs_diff),
    ),
    ReductionOperator(
        "Norm Abs Diff", "Norm Abs Diff", 2,
        _scalar_from_per_frame(_pf_consecutive(_norm_abs_diff)),
        _pf_consecutive(_norm_abs_diff),
    ),
    ReductionOperator(
        "Norm Abs Derivative", "Norm Abs Derivative", 3,
        _scalar_from_per_frame(_pf_norm_abs_derivative),
        _pf_norm_abs_derivative,
    ),
]

REDUCTIONS_BY_NAME: dict[str, ReductionOperator] = {
    op.name: op for op in REDUCTION_OPERATORS
}


def list_operators() -> list[ReductionOperator]:
    """All registered operators, in display order."""
    return list(REDUCTION_OPERATORS)


def operators_for_frame_count(n_frames: int) -> list[ReductionOperator]:
    """Operators applicable to a sub-cube with ``n_frames`` frames."""
    return [op for op in REDUCTION_OPERATORS if n_frames >= op.min_frames]
