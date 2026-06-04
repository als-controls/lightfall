"""Tests for the themed pyqtgraph wrapper (`lightfall.visualization.pg`).

The wrapper is a drop-in replacement for ``import pyqtgraph as pg`` that pulls
default colors from :mod:`lightfall.visualization.theme`. It must:

* Re-export the pyqtgraph namespace.
* Inject palette colors when the caller did not pass an explicit pen/brush.
* Leave explicit pens/brushes alone, even after a retheme.
* Support live retheme via ``retheme_all(colors=...)``.
* Expose a ``series_pen(i)`` helper that cycles through palette line colors.
* Use a weak registry so destroyed items do not leak.
"""

from __future__ import annotations

import gc

import pytest

pytest.importorskip("pyqtgraph")
pytest.importorskip("PySide6")

import pyqtgraph as pg  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from lightfall.visualization import pg as themed_pg  # noqa: E402
from lightfall.visualization.theme import (  # noqa: E402
    DARK_VIZ_COLORS,
    LIGHT_VIZ_COLORS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _color_name(value) -> str:
    """Normalize a pen / brush / color spec to a lowercase ``#rrggbb`` string."""
    if hasattr(value, "color"):  # QPen / QBrush
        return value.color().name().lower()
    return QColor(value).name().lower()


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch):
    """Each test sees a clean weak registry and a deterministic dark palette.

    ``_current_colors()`` consults the ThemeManager singleton, which other
    tests in the same process may have initialized (with a light theme on CI
    runners); pin it so these tests don't depend on execution order.
    """
    monkeypatch.setattr(themed_pg, "_current_colors", lambda: DARK_VIZ_COLORS)
    themed_pg._themed_items.clear()
    yield
    themed_pg._themed_items.clear()


# ---------------------------------------------------------------------------
# Re-exports
# ---------------------------------------------------------------------------


class TestReexports:
    def test_plotwidget_is_pg(self):
        assert themed_pg.PlotWidget is pg.PlotWidget

    def test_mkpen_is_pg(self):
        assert themed_pg.mkPen is pg.mkPen

    def test_imageview_is_pg(self):
        assert themed_pg.ImageView is pg.ImageView


# ---------------------------------------------------------------------------
# PlotDataItem
# ---------------------------------------------------------------------------


class TestPlotDataItem:
    def test_no_pen_uses_primary_line(self, qapp):
        item = themed_pg.PlotDataItem([1, 2, 3])
        assert _color_name(item.opts["pen"]) == DARK_VIZ_COLORS.primary_line.lower()

    def test_explicit_pen_is_kept(self, qapp):
        item = themed_pg.PlotDataItem([1, 2, 3], pen="#ff00ff")
        assert _color_name(item.opts["pen"]) == "#ff00ff"

    def test_retheme_updates_default_pen(self, qapp):
        item = themed_pg.PlotDataItem([1, 2, 3])
        themed_pg.retheme_all(LIGHT_VIZ_COLORS)
        assert _color_name(item.opts["pen"]) == LIGHT_VIZ_COLORS.primary_line.lower()

    def test_retheme_preserves_explicit_pen(self, qapp):
        item = themed_pg.PlotDataItem([1, 2, 3], pen="#ff00ff")
        themed_pg.retheme_all(LIGHT_VIZ_COLORS)
        assert _color_name(item.opts["pen"]) == "#ff00ff"


# ---------------------------------------------------------------------------
# ScatterPlotItem
# ---------------------------------------------------------------------------


class TestScatterPlotItem:
    def test_no_brush_uses_primary_line(self, qapp):
        item = themed_pg.ScatterPlotItem(x=[1, 2, 3], y=[1, 2, 3])
        assert _color_name(item.opts["brush"]) == DARK_VIZ_COLORS.primary_line.lower()

    def test_explicit_brush_is_kept(self, qapp):
        item = themed_pg.ScatterPlotItem(x=[1, 2, 3], y=[1, 2, 3], brush="#ff00ff")
        assert _color_name(item.opts["brush"]) == "#ff00ff"

    def test_retheme_updates_default_brush(self, qapp):
        item = themed_pg.ScatterPlotItem(x=[1, 2, 3], y=[1, 2, 3])
        themed_pg.retheme_all(LIGHT_VIZ_COLORS)
        assert _color_name(item.opts["brush"]) == LIGHT_VIZ_COLORS.primary_line.lower()


# ---------------------------------------------------------------------------
# InfiniteLine
# ---------------------------------------------------------------------------


class TestInfiniteLine:
    def test_no_pen_uses_highlight(self, qapp):
        line = themed_pg.InfiniteLine(pos=0)
        assert _color_name(line.pen) == DARK_VIZ_COLORS.highlight.lower()

    def test_explicit_pen_is_kept(self, qapp):
        line = themed_pg.InfiniteLine(pos=0, pen="#ff00ff")
        assert _color_name(line.pen) == "#ff00ff"


# ---------------------------------------------------------------------------
# series_pen helper
# ---------------------------------------------------------------------------


class TestSeriesPen:
    def test_returns_first_color_for_zero(self, qapp):
        assert themed_pg.series_pen(0).lower() == DARK_VIZ_COLORS.line_colors[0].lower()

    def test_cycles_through_all_colors(self, qapp):
        for i, expected in enumerate(DARK_VIZ_COLORS.line_colors):
            assert themed_pg.series_pen(i).lower() == expected.lower()

    def test_wraps_past_end(self, qapp):
        n = len(DARK_VIZ_COLORS.line_colors)
        assert (
            themed_pg.series_pen(n).lower()
            == DARK_VIZ_COLORS.line_colors[0].lower()
        )
        assert (
            themed_pg.series_pen(n + 3).lower()
            == DARK_VIZ_COLORS.line_colors[3].lower()
        )


# ---------------------------------------------------------------------------
# Weak registry semantics
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_item_registered_on_construction(self, qapp):
        item = themed_pg.PlotDataItem([1, 2, 3])
        assert item in themed_pg._themed_items

    def test_dropped_item_is_collected(self, qapp):
        item = themed_pg.PlotDataItem([1, 2, 3])
        assert len(themed_pg._themed_items) == 1
        del item
        gc.collect()
        assert len(themed_pg._themed_items) == 0

    def test_retheme_skips_collected_items(self, qapp):
        """Calling ``retheme_all`` after items are GC'd must not raise."""
        themed_pg.PlotDataItem([1, 2, 3])  # not bound; collectible
        gc.collect()
        themed_pg.retheme_all(LIGHT_VIZ_COLORS)  # must not raise
