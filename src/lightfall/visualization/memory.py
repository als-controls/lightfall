"""Memory management and decimation for large datasets.

Provides algorithms for reducing data points while preserving visual
fidelity, essential for real-time plotting of large datasets.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from loguru import logger


def decimate_lttb(
    x: np.ndarray,
    y: np.ndarray,
    target_points: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Decimate data using Largest Triangle Three Buckets (LTTB).

    LTTB preserves the visual shape of the data by selecting points
    that maximize the visible area of triangles. This is ideal for
    line plots where the overall shape matters more than exact values.

    Algorithm:
        1. Always keep first and last points
        2. Divide remaining data into (target_points - 2) buckets
        3. For each bucket, select point that forms largest triangle
           with previous selected point and next bucket's average

    Complexity: O(n) where n is input size

    Args:
        x: X values (1D array).
        y: Y values (1D array, same length as x).
        target_points: Desired number of output points.

    Returns:
        Tuple of (decimated_x, decimated_y) arrays.

    Reference:
        Sveinn Steinarsson, "Downsampling Time Series for Visual
        Representation", 2013.
    """
    n = len(x)

    if n <= target_points:
        return x.copy(), y.copy()

    if target_points < 3:
        # Need at least 3 points for LTTB
        return x[[0, -1]], y[[0, -1]]

    # Pre-allocate output arrays
    out_x = np.empty(target_points)
    out_y = np.empty(target_points)

    # Always include first point
    out_x[0] = x[0]
    out_y[0] = y[0]

    # Bucket size
    bucket_size = (n - 2) / (target_points - 2)

    a = 0  # Previous selected point index

    for i in range(target_points - 2):
        # Calculate bucket boundaries
        bucket_start = int((i + 1) * bucket_size) + 1
        bucket_end = int((i + 2) * bucket_size) + 1
        bucket_end = min(bucket_end, n - 1)

        # Calculate average of next bucket for triangle calculation
        next_bucket_start = bucket_end
        next_bucket_end = int((i + 3) * bucket_size) + 1
        next_bucket_end = min(next_bucket_end, n)

        avg_x = np.mean(x[next_bucket_start:next_bucket_end])
        avg_y = np.mean(y[next_bucket_start:next_bucket_end])

        # Find point in current bucket that maximizes triangle area
        max_area = -1.0
        max_idx = bucket_start

        for j in range(bucket_start, bucket_end):
            # Triangle area = 0.5 * |x1(y2-y3) + x2(y3-y1) + x3(y1-y2)|
            area = abs(
                (x[a] - avg_x) * (y[j] - out_y[i])
                - (x[a] - x[j]) * (avg_y - out_y[i])
            )
            if area > max_area:
                max_area = area
                max_idx = j

        out_x[i + 1] = x[max_idx]
        out_y[i + 1] = y[max_idx]
        a = max_idx

    # Always include last point
    out_x[-1] = x[-1]
    out_y[-1] = y[-1]

    return out_x, out_y


def decimate_minmax(
    x: np.ndarray,
    y: np.ndarray,
    target_points: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Decimate data using min-max preservation.

    For each bucket, keeps both the minimum and maximum Y values.
    This preserves peaks and valleys, ideal for detecting features
    in noisy data or showing data envelopes.

    Note: Returns up to 2 * target_points since both min and max
    are kept per bucket.

    Args:
        x: X values (1D array).
        y: Y values (1D array, same length as x).
        target_points: Approximate desired buckets (actual points ~2x).

    Returns:
        Tuple of (decimated_x, decimated_y) arrays.
    """
    n = len(x)

    if n <= target_points * 2:
        return x.copy(), y.copy()

    # Number of buckets
    n_buckets = target_points

    # Pre-allocate for worst case (2 points per bucket + first/last)
    out_x = []
    out_y = []

    # Always include first point
    out_x.append(x[0])
    out_y.append(y[0])

    bucket_size = n / n_buckets

    for i in range(n_buckets):
        start = int(i * bucket_size)
        end = int((i + 1) * bucket_size)
        end = min(end, n)

        if start >= end:
            continue

        bucket_y = y[start:end]
        bucket_x = x[start:end]

        min_idx = np.argmin(bucket_y)
        max_idx = np.argmax(bucket_y)

        # Add in order of x value
        if min_idx < max_idx:
            out_x.append(bucket_x[min_idx])
            out_y.append(bucket_y[min_idx])
            if min_idx != max_idx:
                out_x.append(bucket_x[max_idx])
                out_y.append(bucket_y[max_idx])
        else:
            out_x.append(bucket_x[max_idx])
            out_y.append(bucket_y[max_idx])
            if min_idx != max_idx:
                out_x.append(bucket_x[min_idx])
                out_y.append(bucket_y[min_idx])

    # Always include last point
    if out_x[-1] != x[-1]:
        out_x.append(x[-1])
        out_y.append(y[-1])

    return np.array(out_x), np.array(out_y)


def decimate_uniform(
    x: np.ndarray,
    y: np.ndarray,
    target_points: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Decimate data by uniform sampling.

    Simple decimation that takes every Nth point. Fast but may miss
    important features. Best for data that's already well-sampled.

    Args:
        x: X values (1D array).
        y: Y values (1D array, same length as x).
        target_points: Desired number of output points.

    Returns:
        Tuple of (decimated_x, decimated_y) arrays.
    """
    n = len(x)

    if n <= target_points:
        return x.copy(), y.copy()

    # Calculate step size
    step = max(1, n // target_points)

    indices = np.arange(0, n, step)

    # Ensure last point is included
    if indices[-1] != n - 1:
        indices = np.append(indices, n - 1)

    return x[indices], y[indices]


def auto_decimate(
    x: np.ndarray,
    y: np.ndarray,
    target_points: int = 2000,
    method: str = "lttb",
) -> tuple[np.ndarray, np.ndarray]:
    """Automatically decimate data if needed.

    Chooses decimation method and only decimates if data exceeds
    target size.

    Args:
        x: X values.
        y: Y values.
        target_points: Target number of points.
        method: Decimation method ("lttb", "minmax", "uniform").

    Returns:
        Tuple of (x, y) arrays, possibly decimated.
    """
    if len(x) <= target_points:
        return x, y

    if method == "lttb":
        return decimate_lttb(x, y, target_points)
    elif method == "minmax":
        return decimate_minmax(x, y, target_points)
    elif method == "uniform":
        return decimate_uniform(x, y, target_points)
    else:
        logger.warning("Unknown decimation method '{}', using lttb", method)
        return decimate_lttb(x, y, target_points)


class StreamingDecimator:
    """Streaming decimator for incremental data.

    Maintains a decimated view of incrementally received data,
    suitable for live plotting during scans.

    Example:
        >>> decimator = StreamingDecimator(max_display_points=2000)
        >>> for x, y in data_stream:
        ...     decimator.add_point(x, y)
        ...     plot.setData(*decimator.get_display_data())
    """

    def __init__(
        self,
        max_display_points: int = 2000,
        method: str = "lttb",
    ) -> None:
        """Initialize the streaming decimator.

        Args:
            max_display_points: Maximum points to return for display.
            method: Decimation method.
        """
        self._max_display = max_display_points
        self._method = method

        # Full data storage (may grow unbounded - use with ring buffer)
        self._x: list[float] = []
        self._y: list[float] = []

        # Cached decimated data
        self._display_x: np.ndarray | None = None
        self._display_y: np.ndarray | None = None
        self._dirty = True

    def add_point(self, x: float, y: float) -> None:
        """Add a new data point.

        Args:
            x: X value.
            y: Y value.
        """
        self._x.append(x)
        self._y.append(y)
        self._dirty = True

    def add_points(self, x: Any, y: Any) -> None:
        """Add multiple data points.

        Args:
            x: X values (array-like).
            y: Y values (array-like).
        """
        self._x.extend(x)
        self._y.extend(y)
        self._dirty = True

    def get_display_data(self) -> tuple[np.ndarray, np.ndarray]:
        """Get decimated data for display.

        Returns:
            Tuple of (x, y) arrays suitable for plotting.
        """
        if not self._x:
            return np.array([]), np.array([])

        if self._dirty or self._display_x is None:
            x = np.array(self._x)
            y = np.array(self._y)
            self._display_x, self._display_y = auto_decimate(
                x, y, self._max_display, self._method
            )
            self._dirty = False

        return self._display_x, self._display_y

    def get_full_data(self) -> tuple[np.ndarray, np.ndarray]:
        """Get full (non-decimated) data.

        Returns:
            Tuple of (x, y) arrays with all data.
        """
        return np.array(self._x), np.array(self._y)

    def clear(self) -> None:
        """Clear all data."""
        self._x.clear()
        self._y.clear()
        self._display_x = None
        self._display_y = None
        self._dirty = True

    @property
    def point_count(self) -> int:
        """Get total number of points stored."""
        return len(self._x)

    @property
    def display_point_count(self) -> int:
        """Get number of points in display data."""
        if self._display_x is None:
            return 0
        return len(self._display_x)


def estimate_memory_usage(
    num_points: int,
    num_fields: int,
    bytes_per_value: int = 8,
) -> int:
    """Estimate memory usage for data storage.

    Args:
        num_points: Number of data points.
        num_fields: Number of fields per point.
        bytes_per_value: Bytes per value (default 8 for float64).

    Returns:
        Estimated memory usage in bytes.
    """
    return num_points * num_fields * bytes_per_value


def suggest_max_points(
    target_memory_mb: float = 100,
    num_fields: int = 10,
    bytes_per_value: int = 8,
) -> int:
    """Suggest maximum points based on memory budget.

    Args:
        target_memory_mb: Target memory usage in MB.
        num_fields: Number of fields per point.
        bytes_per_value: Bytes per value.

    Returns:
        Suggested maximum number of points.
    """
    target_bytes = target_memory_mb * 1024 * 1024
    return int(target_bytes / (num_fields * bytes_per_value))
