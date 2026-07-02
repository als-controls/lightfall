"""Curve fitting system for visualization widgets.

Provides a modular fitting framework with common fit functions and
a UI panel for interactive fitting.
"""

from __future__ import annotations

from lightfall.visualization.fitting.base import BaseFitter, FitResult
from lightfall.visualization.fitting.fitters import (
    GaussianFitter,
    LinearFitter,
    LorentzianFitter,
    PolynomialFitter,
    StepFitter,
)

__all__ = [
    "BaseFitter",
    "FitResult",
    "LinearFitter",
    "GaussianFitter",
    "LorentzianFitter",
    "PolynomialFitter",
    "StepFitter",
]
