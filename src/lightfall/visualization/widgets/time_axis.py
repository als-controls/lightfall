"""Human-readable time axis for pyqtgraph plots.

Provides an axis that formats time values with appropriate units,
auto-scaling between milliseconds, seconds, minutes, and hours.
"""

from __future__ import annotations

import pyqtgraph as pg


class HumanReadableTimeAxis(pg.AxisItem):
    """Axis that formats time values with appropriate units.

    Auto-scales between:
    - < 1s: milliseconds (e.g., "500 ms")
    - < 60s: seconds (e.g., "45.2 s")
    - < 60min: minutes:seconds (e.g., "5:30")
    - >= 60min: hours:minutes:seconds (e.g., "1:30:00")

    Example:
        >>> axis = HumanReadableTimeAxis(orientation='bottom')
        >>> plot.setAxisItems({'bottom': axis})
    """

    def __init__(self, orientation: str = "bottom", **kwargs) -> None:
        """Initialize the time axis.

        Args:
            orientation: Axis orientation ('left', 'right', 'top', 'bottom').
            **kwargs: Additional arguments passed to AxisItem.
        """
        super().__init__(orientation, **kwargs)
        self.setLabel("Frame 0/0")

    def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:
        """Format tick values as human-readable time strings.

        Args:
            values: List of tick values in seconds.
            scale: Scale factor (typically 1.0).
            spacing: Spacing between ticks.

        Returns:
            List of formatted time strings.
        """
        if not values:
            return []

        # Determine the range to pick appropriate format
        max_val = max(abs(v) for v in values) if values else 0

        strings = []
        for v in values:
            strings.append(self._format_time(v, max_val))

        return strings

    def _format_time(self, seconds: float, max_seconds: float) -> str:
        """Format a time value based on the overall range.

        Args:
            seconds: Time value in seconds.
            max_seconds: Maximum time value in the current view.

        Returns:
            Formatted time string.
        """
        if max_seconds < 1.0:
            # Show milliseconds
            ms = seconds * 1000
            if abs(ms) < 10:
                return f"{ms:.1f} ms"
            return f"{ms:.0f} ms"

        elif max_seconds < 60:
            # Show seconds
            if max_seconds < 10:
                return f"{seconds:.2f} s"
            return f"{seconds:.1f} s"

        elif max_seconds < 3600:
            # Show minutes:seconds
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}:{secs:02d}"

        else:
            # Show hours:minutes:seconds
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            return f"{hours}:{minutes:02d}:{secs:02d}"
