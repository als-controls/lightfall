"""Themed pyqtgraph drop-in for lightfall visualizations.

Usage in viz widgets::

    from lightfall.visualization import pg

    self._plot = pg.PlotWidget()
    curve = pg.PlotDataItem(xs, ys)              # picks up theme color
    self._plot.addItem(pg.InfiniteLine(pos=0))   # picks up highlight color

The module re-exports the entire ``pyqtgraph`` namespace, so anything that
``import pyqtgraph as pg`` provided still works. The themed subclasses
(:class:`PlotDataItem`, :class:`ScatterPlotItem`, :class:`InfiniteLine`)
shadow the originals and inject palette colors when the caller did not pass
an explicit ``pen`` / ``brush``.

Background, axis, tick, grid, and default text colors are handled globally by
:func:`lightfall.visualization.theme.apply_pyqtgraph_theme`, which the main window
calls on startup and on every theme change. On theme change the main window
also calls :func:`retheme_all`, which walks the weak registry of live themed
items and re-applies the new palette to those that still use defaults.
"""

from __future__ import annotations

import weakref
from typing import Any

import pyqtgraph as _pg
from pyqtgraph import *  # noqa: F401,F403  -- re-export full namespace

from lightfall.visualization.theme import (
    DARK_VIZ_COLORS,
    VisualizationColors,
    colors_from_theme,
)

__all__ = [
    "PlotDataItem",
    "ScatterPlotItem",
    "InfiniteLine",
    "retheme_all",
    "series_pen",
]


# Weak registry of themed items still alive. retheme_all() walks this set on
# theme change. weakref.WeakSet drops entries automatically when the underlying
# QGraphicsObject is collected.
_themed_items: weakref.WeakSet = weakref.WeakSet()


def _current_colors() -> VisualizationColors:
    """Best-effort fetch of the current theme palette.

    Falls back to dark defaults if the theme manager is unavailable (tests,
    headless tooling, early import order).
    """
    try:
        from lightfall.ui.theme import ThemeManager

        tm = ThemeManager.get_instance()
        return colors_from_theme(tm.colors, tm.is_dark)
    except Exception:
        return DARK_VIZ_COLORS


# ---------------------------------------------------------------------------
# Themed item subclasses
# ---------------------------------------------------------------------------


class PlotDataItem(_pg.PlotDataItem):
    """:class:`pyqtgraph.PlotDataItem` whose default pen comes from the theme.

    If the caller passes ``pen=`` explicitly the value is preserved across
    retheme; otherwise the pen tracks ``VisualizationColors.primary_line``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._user_pen = "pen" in kwargs
        super().__init__(*args, **kwargs)
        _themed_items.add(self)
        if not self._user_pen:
            self._apply_palette(_current_colors())

    def _apply_palette(self, colors: VisualizationColors) -> None:
        if not self._user_pen:
            self.setPen(colors.primary_line)


class ScatterPlotItem(_pg.ScatterPlotItem):
    """:class:`pyqtgraph.ScatterPlotItem` whose default brush comes from the theme."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._user_brush = "brush" in kwargs
        self._user_pen = "pen" in kwargs
        super().__init__(*args, **kwargs)
        _themed_items.add(self)
        if not self._user_brush:
            self._apply_palette(_current_colors())

    def _apply_palette(self, colors: VisualizationColors) -> None:
        if not self._user_brush:
            self.setBrush(colors.primary_line)


class InfiniteLine(_pg.InfiniteLine):
    """:class:`pyqtgraph.InfiniteLine` whose default pen tracks the highlight color.

    Used for crosshairs, ROI guides, and similar overlays that should pop
    against the background regardless of theme.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._user_pen = "pen" in kwargs
        super().__init__(*args, **kwargs)
        _themed_items.add(self)
        if not self._user_pen:
            self._apply_palette(_current_colors())

    def _apply_palette(self, colors: VisualizationColors) -> None:
        if not self._user_pen:
            self.setPen(colors.highlight)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def series_pen(index: int) -> str:
    """Return the *index*-th palette color for a multi-series plot.

    Cycles through :attr:`VisualizationColors.line_colors`. Returns a hex
    string suitable for passing to ``pen=`` in any pyqtgraph constructor.
    """
    colors = _current_colors().line_colors
    return colors[index % len(colors)]


def retheme_all(colors: VisualizationColors | None = None) -> None:
    """Re-apply the current (or given) palette to every live themed item.

    Wire to :attr:`ThemeManager.colors_changed` so live theme switches update
    on-screen plots without needing to recreate widgets. Items that were
    constructed with explicit pens/brushes keep their caller-chosen colors.
    """
    palette = colors if colors is not None else _current_colors()
    # Snapshot to a list -- _themed_items is weak and may shrink during iteration.
    for item in list(_themed_items):
        try:
            item._apply_palette(palette)
        except RuntimeError:
            # Underlying C++ object already deleted; weak ref will clear shortly.
            continue
