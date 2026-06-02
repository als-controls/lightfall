"""Curve fitting system for visualization widgets.

Provides a modular fitting framework with common fit functions and
a UI panel for interactive fitting.
"""

from __future__ import annotations

from lucid.visualization.fitting.base import BaseFitter, FitResult
from lucid.visualization.fitting.fitters import (
    GaussianFitter,
    LinearFitter,
    LorentzianFitter,
    PolynomialFitter,
)

__all__ = [
    "BaseFitter",
    "FitResult",
    "LinearFitter",
    "GaussianFitter",
    "LorentzianFitter",
    "PolynomialFitter",
]
