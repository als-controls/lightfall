"""Concrete fitter implementations.

Provides common fit functions: Linear, Gaussian, Lorentzian, Polynomial.
"""

from __future__ import annotations

import numpy as np

from lucid.visualization.fitting.base import BaseFitter


class LinearFitter(BaseFitter):
    """Linear fit: y = mx + b."""

    @property
    def name(self) -> str:
        return "linear"

    @property
    def display_name(self) -> str:
        return "Linear"

    @property
    def parameter_names(self) -> list[str]:
        return ["slope", "intercept"]

    def model(self, x: np.ndarray, slope: float, intercept: float) -> np.ndarray:
        return slope * x + intercept

    def estimate_initial(self, x: np.ndarray, y: np.ndarray) -> list[float]:
        # Simple linear regression for initial estimate
        if len(x) < 2:
            return [0.0, y.mean() if len(y) > 0 else 0.0]

        x_mean = x.mean()
        y_mean = y.mean()
        slope = np.sum((x - x_mean) * (y - y_mean)) / np.sum((x - x_mean)**2)
        intercept = y_mean - slope * x_mean
        return [slope, intercept]

    def get_formula(self) -> str:
        return "y = mx + b"


class GaussianFitter(BaseFitter):
    """Gaussian peak: y = amplitude * exp(-(x-center)²/(2*sigma²)) + background."""

    @property
    def name(self) -> str:
        return "gaussian"

    @property
    def display_name(self) -> str:
        return "Gaussian Peak"

    @property
    def parameter_names(self) -> list[str]:
        return ["amplitude", "center", "sigma", "background"]

    def model(
        self,
        x: np.ndarray,
        amplitude: float,
        center: float,
        sigma: float,
        background: float,
    ) -> np.ndarray:
        return amplitude * np.exp(-((x - center)**2) / (2 * sigma**2)) + background

    def estimate_initial(self, x: np.ndarray, y: np.ndarray) -> list[float]:
        # Background: minimum value
        background = y.min()

        # Amplitude: max - min
        amplitude = y.max() - background

        # Center: x at maximum y
        center = x[np.argmax(y)]

        # Sigma: estimate from FWHM
        # Find half-max points
        half_max = background + amplitude / 2
        above_half = y > half_max
        if np.any(above_half):
            indices = np.where(above_half)[0]
            if len(indices) > 1:
                fwhm = x[indices[-1]] - x[indices[0]]
                sigma = fwhm / (2 * np.sqrt(2 * np.log(2)))  # FWHM = 2.355 * sigma
            else:
                sigma = (x.max() - x.min()) / 10
        else:
            sigma = (x.max() - x.min()) / 10

        return [amplitude, center, max(sigma, 1e-6), background]

    def get_bounds(self) -> tuple[list[float], list[float]]:
        # Amplitude > 0, sigma > 0
        return (
            [0, -np.inf, 1e-10, -np.inf],  # Lower bounds
            [np.inf, np.inf, np.inf, np.inf],  # Upper bounds
        )

    def get_formula(self) -> str:
        return "y = A·exp(-(x-μ)²/(2σ²)) + b"


class LorentzianFitter(BaseFitter):
    """Lorentzian peak: y = amplitude * gamma² / ((x-center)² + gamma²) + background."""

    @property
    def name(self) -> str:
        return "lorentzian"

    @property
    def display_name(self) -> str:
        return "Lorentzian Peak"

    @property
    def parameter_names(self) -> list[str]:
        return ["amplitude", "center", "gamma", "background"]

    def model(
        self,
        x: np.ndarray,
        amplitude: float,
        center: float,
        gamma: float,
        background: float,
    ) -> np.ndarray:
        return amplitude * gamma**2 / ((x - center)**2 + gamma**2) + background

    def estimate_initial(self, x: np.ndarray, y: np.ndarray) -> list[float]:
        # Similar to Gaussian initial estimates
        background = y.min()
        amplitude = y.max() - background
        center = x[np.argmax(y)]

        # Gamma: estimate from half-max width
        half_max = background + amplitude / 2
        above_half = y > half_max
        if np.any(above_half):
            indices = np.where(above_half)[0]
            if len(indices) > 1:
                hwhm = (x[indices[-1]] - x[indices[0]]) / 2
                gamma = max(hwhm, 1e-6)
            else:
                gamma = (x.max() - x.min()) / 10
        else:
            gamma = (x.max() - x.min()) / 10

        return [amplitude, center, gamma, background]

    def get_bounds(self) -> tuple[list[float], list[float]]:
        return (
            [0, -np.inf, 1e-10, -np.inf],
            [np.inf, np.inf, np.inf, np.inf],
        )

    def get_formula(self) -> str:
        return "y = A·γ²/((x-x₀)² + γ²) + b"


class PolynomialFitter(BaseFitter):
    """Polynomial fit: y = a₀ + a₁x + a₂x² + ... + aₙxⁿ."""

    def __init__(self, degree: int = 2) -> None:
        """Initialize polynomial fitter.

        Args:
            degree: Polynomial degree (default 2 for quadratic).
        """
        self._degree = max(1, degree)

    @property
    def name(self) -> str:
        return f"polynomial_{self._degree}"

    @property
    def display_name(self) -> str:
        names = {1: "Linear", 2: "Quadratic", 3: "Cubic"}
        return names.get(self._degree, f"Polynomial (degree {self._degree})")

    @property
    def parameter_names(self) -> list[str]:
        return [f"a{i}" for i in range(self._degree + 1)]

    def model(self, x: np.ndarray, *coeffs) -> np.ndarray:
        result = np.zeros_like(x, dtype=float)
        for i, c in enumerate(coeffs):
            result += c * (x ** i)
        return result

    def estimate_initial(self, x: np.ndarray, y: np.ndarray) -> list[float]:
        # Use numpy polyfit for initial estimate
        try:
            coeffs = np.polyfit(x, y, self._degree)
            # polyfit returns highest degree first, reverse it
            return list(reversed(coeffs))
        except Exception:
            return [0.0] * (self._degree + 1)

    def get_formula(self) -> str:
        terms = []
        for i in range(self._degree + 1):
            if i == 0:
                terms.append("a₀")
            elif i == 1:
                terms.append("a₁x")
            else:
                terms.append(f"a{i}x^{i}")
        return "y = " + " + ".join(terms)


class VoigtFitter(BaseFitter):
    """Voigt profile: convolution of Gaussian and Lorentzian.

    Approximated using the Faddeeva function.
    """

    @property
    def name(self) -> str:
        return "voigt"

    @property
    def display_name(self) -> str:
        return "Voigt Profile"

    @property
    def parameter_names(self) -> list[str]:
        return ["amplitude", "center", "sigma", "gamma", "background"]

    def model(
        self,
        x: np.ndarray,
        amplitude: float,
        center: float,
        sigma: float,
        gamma: float,
        background: float,
    ) -> np.ndarray:
        try:
            from scipy.special import voigt_profile

            # Voigt profile using scipy
            return amplitude * voigt_profile(x - center, sigma, gamma) + background
        except ImportError:
            # Fallback to pseudo-Voigt approximation
            # Pseudo-Voigt: linear combination of Gaussian and Lorentzian
            gauss = np.exp(-((x - center)**2) / (2 * sigma**2))
            lorentz = gamma**2 / ((x - center)**2 + gamma**2)
            eta = gamma / (sigma + gamma)  # Mixing parameter
            return amplitude * ((1 - eta) * gauss + eta * lorentz) + background

    def estimate_initial(self, x: np.ndarray, y: np.ndarray) -> list[float]:
        # Similar to Gaussian
        background = y.min()
        amplitude = y.max() - background
        center = x[np.argmax(y)]
        sigma = (x.max() - x.min()) / 20
        gamma = sigma  # Start with equal contributions

        return [amplitude, center, max(sigma, 1e-6), max(gamma, 1e-6), background]

    def get_bounds(self) -> tuple[list[float], list[float]]:
        return (
            [0, -np.inf, 1e-10, 1e-10, -np.inf],
            [np.inf, np.inf, np.inf, np.inf, np.inf],
        )

    def get_formula(self) -> str:
        return "y = A·V(x-x₀; σ, γ) + b"


# Registry of available fitters
AVAILABLE_FITTERS: dict[str, type[BaseFitter]] = {
    "linear": LinearFitter,
    "gaussian": GaussianFitter,
    "lorentzian": LorentzianFitter,
    "polynomial_2": lambda: PolynomialFitter(2),
    "polynomial_3": lambda: PolynomialFitter(3),
    "voigt": VoigtFitter,
}


def get_fitter(name: str) -> BaseFitter:
    """Get a fitter instance by name.

    Args:
        name: Fitter name (e.g., "gaussian", "linear").

    Returns:
        Fitter instance.

    Raises:
        ValueError: If fitter not found.
    """
    if name not in AVAILABLE_FITTERS:
        raise ValueError(f"Unknown fitter: {name}. Available: {list(AVAILABLE_FITTERS.keys())}")

    fitter_factory = AVAILABLE_FITTERS[name]
    if callable(fitter_factory) and not isinstance(fitter_factory, type):
        # It's a factory function (like for polynomial)
        return fitter_factory()
    return fitter_factory()


def list_fitters() -> list[dict[str, str]]:
    """List all available fitters.

    Returns:
        List of dicts with 'name' and 'display_name'.
    """
    result = []
    for _name, factory in AVAILABLE_FITTERS.items():
        if callable(factory) and not isinstance(factory, type):
            fitter = factory()
        else:
            fitter = factory()
        result.append({
            "name": fitter.name,
            "display_name": fitter.display_name,
            "formula": fitter.get_formula(),
        })
    return result
