"""Tests for the error-function step/edge fitter."""
from __future__ import annotations

import numpy as np
import pytest

from lightfall.visualization.fitting.fitters import StepFitter, get_fitter


def _edge(x, amplitude, center, width, background):
    from scipy.special import erf

    return background + amplitude * 0.5 * (1.0 + erf((x - center) / (np.sqrt(2.0) * width)))


def test_step_registered():
    fitter = get_fitter("step")
    assert isinstance(fitter, StepFitter)
    assert "center" in fitter.parameter_names


def test_step_fits_rising_edge_center():
    x = np.linspace(-5, 5, 81)
    y = _edge(x, amplitude=100.0, center=1.3, width=0.4, background=5.0)
    result = StepFitter().fit(x, y)
    assert result.success
    assert result.parameters["center"] == pytest.approx(1.3, abs=0.05)
    assert result.parameters["amplitude"] == pytest.approx(100.0, rel=0.05)
    assert result.r_squared > 0.99


def test_step_fits_falling_edge_center():
    # Falling edge -> negative amplitude; center still recovered.
    x = np.linspace(-5, 5, 81)
    y = _edge(x, amplitude=-80.0, center=-0.7, width=0.5, background=90.0)
    result = StepFitter().fit(x, y)
    assert result.success
    assert result.parameters["center"] == pytest.approx(-0.7, abs=0.05)
    assert result.parameters["amplitude"] < 0  # falling
    assert result.r_squared > 0.99


def test_step_fit_robust_to_noise():
    rng = np.random.default_rng(0)
    x = np.linspace(0, 10, 101)
    y = _edge(x, amplitude=50.0, center=6.2, width=0.6, background=10.0)
    y_noisy = y + rng.normal(0, 1.0, size=y.shape)
    result = StepFitter().fit(x, y_noisy)
    assert result.success
    assert result.parameters["center"] == pytest.approx(6.2, abs=0.15)
