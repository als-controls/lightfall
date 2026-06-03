"""Base classes for curve fitting.

Provides the abstract fitter interface and fit result container.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class FitResult:
    """Result of a curve fitting operation.

    Attributes:
        success: Whether the fit converged successfully.
        parameters: Dict mapping parameter names to fitted values.
        errors: Dict mapping parameter names to standard errors.
        r_squared: Coefficient of determination (R²).
        residuals: Array of fit residuals.
        chi_squared: Chi-squared statistic.
        x_fit: X values for plotting the fit curve.
        y_fit: Y values for plotting the fit curve.
        method: Name of the fitting method used.
        info: Additional fit information (iterations, etc.).
    """

    success: bool = False
    parameters: dict[str, float] = field(default_factory=dict)
    errors: dict[str, float] = field(default_factory=dict)
    r_squared: float = 0.0
    residuals: np.ndarray | None = None
    chi_squared: float = 0.0
    x_fit: np.ndarray | None = None
    y_fit: np.ndarray | None = None
    method: str = ""
    info: dict[str, Any] = field(default_factory=dict)

    def get_parameter(self, name: str) -> tuple[float, float]:
        """Get a parameter value and its error.

        Args:
            name: Parameter name.

        Returns:
            Tuple of (value, error).
        """
        return self.parameters.get(name, 0.0), self.errors.get(name, 0.0)

    def format_parameter(self, name: str, precision: int = 4) -> str:
        """Format a parameter as "value ± error".

        Args:
            name: Parameter name.
            precision: Number of significant figures.

        Returns:
            Formatted string.
        """
        val, err = self.get_parameter(name)
        if err > 0:
            return f"{val:.{precision}g} ± {err:.{precision}g}"
        return f"{val:.{precision}g}"

    def as_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "parameters": self.parameters,
            "errors": self.errors,
            "r_squared": self.r_squared,
            "chi_squared": self.chi_squared,
            "method": self.method,
            "info": self.info,
        }


class BaseFitter(ABC):
    """Abstract base class for curve fitters.

    Subclasses implement specific fit functions (linear, Gaussian, etc.)
    with initial parameter estimation and constraints.

    Example:
        >>> fitter = GaussianFitter()
        >>> result = fitter.fit(x_data, y_data)
        >>> if result.success:
        ...     print(f"Center: {result.format_parameter('center')}")
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short name for this fitter (e.g., "gaussian")."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name (e.g., "Gaussian Peak")."""
        ...

    @property
    @abstractmethod
    def parameter_names(self) -> list[str]:
        """Names of fit parameters in order."""
        ...

    @property
    def description(self) -> str:
        """Description of the fit function."""
        return self.display_name

    @abstractmethod
    def model(self, x: np.ndarray, *params) -> np.ndarray:
        """Evaluate the fit function.

        Args:
            x: Independent variable values.
            *params: Parameter values in order from parameter_names.

        Returns:
            Model values at x.
        """
        ...

    @abstractmethod
    def estimate_initial(
        self, x: np.ndarray, y: np.ndarray
    ) -> list[float]:
        """Estimate initial parameters from data.

        Good initial estimates are crucial for nonlinear fits.
        This method should analyze the data and return reasonable
        starting values.

        Args:
            x: X data.
            y: Y data.

        Returns:
            List of initial parameter values.
        """
        ...

    def get_bounds(self) -> tuple[list[float], list[float]] | None:
        """Get parameter bounds for constrained fitting.

        Returns:
            Tuple of (lower_bounds, upper_bounds) lists, or None
            for unconstrained fitting.
        """
        return None

    def fit(
        self,
        x: np.ndarray,
        y: np.ndarray,
        initial: list[float] | None = None,
        weights: np.ndarray | None = None,
        **kwargs: Any,
    ) -> FitResult:
        """Perform the curve fit.

        Args:
            x: X data (independent variable).
            y: Y data (dependent variable).
            initial: Initial parameter guesses (auto-estimated if None).
            weights: Optional weights for weighted least squares.
            **kwargs: Additional arguments for scipy.optimize.curve_fit.

        Returns:
            FitResult with fitted parameters and statistics.
        """
        from scipy.optimize import curve_fit

        result = FitResult(method=self.name)

        try:
            # Ensure arrays
            x = np.asarray(x, dtype=float)
            y = np.asarray(y, dtype=float)

            # Remove NaN/inf
            mask = np.isfinite(x) & np.isfinite(y)
            x = x[mask]
            y = y[mask]

            if len(x) < len(self.parameter_names):
                result.info["error"] = "Not enough data points"
                return result

            # Get initial parameters
            if initial is None:
                initial = self.estimate_initial(x, y)

            # Get bounds
            bounds = self.get_bounds()
            if bounds is None:
                bounds = (-np.inf, np.inf)

            # Prepare sigma (inverse weights)
            sigma = None
            if weights is not None:
                # Convert weights to sigma (std dev)
                sigma = 1.0 / np.sqrt(np.maximum(weights, 1e-10))

            # Perform fit
            popt, pcov = curve_fit(
                self.model,
                x,
                y,
                p0=initial,
                bounds=bounds,
                sigma=sigma,
                absolute_sigma=True,
                maxfev=kwargs.get("maxfev", 5000),
            )

            # Extract parameter errors from covariance
            perr = np.sqrt(np.diag(pcov))

            # Store parameters
            for i, name in enumerate(self.parameter_names):
                result.parameters[name] = float(popt[i])
                result.errors[name] = float(perr[i])

            # Calculate fit curve
            x_fit = np.linspace(x.min(), x.max(), 200)
            y_fit = self.model(x_fit, *popt)
            result.x_fit = x_fit
            result.y_fit = y_fit

            # Calculate residuals
            y_pred = self.model(x, *popt)
            result.residuals = y - y_pred

            # Calculate R²
            ss_res = np.sum(result.residuals**2)
            ss_tot = np.sum((y - y.mean())**2)
            result.r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

            # Calculate chi-squared
            if sigma is not None:
                result.chi_squared = float(np.sum((result.residuals / sigma)**2))
            else:
                result.chi_squared = float(ss_res)

            result.success = True
            result.info["nfev"] = "converged"

        except Exception as e:
            result.info["error"] = str(e)

        return result

    def get_formula(self) -> str:
        """Get the mathematical formula as a string.

        Returns:
            LaTeX-style formula string.
        """
        return ""
