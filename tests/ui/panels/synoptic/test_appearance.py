"""Tests for outline-only rendering, the fill toggle, double-rect shapes,
and label z-ordering in the synoptic view."""

import pytest
from PySide6.QtCore import QRectF

from lightfall.ui.panels.synoptic.items import Device2DItem
from lightfall.ui.panels.synoptic.models import (
    DeviceSynopticData,
    PrimitiveShape,
    SynopticViewState,
)
from lightfall.ui.panels.synoptic.view import SynopticView


@pytest.fixture
def view(qtbot):
    v = SynopticView()
    qtbot.addWidget(v)
    return v


def _item(shape=PrimitiveShape.SQUARE, color=(0.3, 0.5, 0.8, 1.0)):
    data = DeviceSynopticData(
        position=(1.0, 0.0, 0.0), primitive_shape=shape, color=color
    )
    return Device2DItem("d1", "dev", data)


# --- outline-only default + fill toggle ---------------------------------


def test_default_style_has_no_fill():
    fill, pen = _item()._effective_style()
    assert fill is None


def test_outline_carries_device_color():
    fill, pen = _item()._effective_style()
    color = pen.color()
    assert color.blueF() == pytest.approx(0.8, abs=0.01)
    assert color.redF() == pytest.approx(0.3, abs=0.01)


def test_fill_toggle_restores_device_color_fill():
    item = _item()
    item.set_fill_visible(True)
    fill, pen = item._effective_style()
    assert fill is not None
    assert fill.blueF() == pytest.approx(0.8, abs=0.01)


def test_offline_dims_outline():
    from lightfall.devices.model import DeviceStatus

    item = _item()
    item.set_device_status(DeviceStatus.OFFLINE)
    fill, pen = item._effective_style()
    assert pen.color().alphaF() < 0.5


def test_status_edge_still_overrides_outline_color():
    from lightfall.devices.model import DeviceStatus

    item = _item()
    item.set_device_status(DeviceStatus.ERROR)
    fill, pen = item._effective_style()
    assert pen.color().red() > 200 and pen.color().green() < 100


def test_view_propagates_fill_to_items(view):
    item = _item()
    view.add_device_item("d1", item)
    view.set_fill_visible(True)
    assert item._effective_style()[0] is not None
    view.set_fill_visible(False)
    assert item._effective_style()[0] is None


def test_fill_state_added_to_items_added_later(view):
    view.set_fill_visible(True)
    item = _item()
    view.add_device_item("d1", item)
    assert item._effective_style()[0] is not None


def test_view_state_round_trips_fill_visible():
    state = SynopticViewState(fill_visible=True)
    assert SynopticViewState.from_dict(state.to_dict()).fill_visible is True
    # default off
    assert SynopticViewState.from_dict({}).fill_visible is False


# --- double-rect shapes ---------------------------------------------------


def test_double_rect_shapes_exist():
    assert PrimitiveShape.DOUBLE_RECT_H == "double_rect_h"
    assert PrimitiveShape.DOUBLE_RECT_V == "double_rect_v"
    # normalize passes them through untouched
    assert PrimitiveShape.normalize(PrimitiveShape.DOUBLE_RECT_H) == PrimitiveShape.DOUBLE_RECT_H


def test_double_rect_blade_geometry():
    item_h = _item(shape=PrimitiveShape.DOUBLE_RECT_H)
    blades = item_h._blade_rects(QRectF(0, 0, 10.0, 4.0))
    assert len(blades) == 2
    left, right = blades
    assert left.left() == 0 and right.right() == pytest.approx(10.0)
    assert right.left() > left.right()  # a gap between the blades

    item_v = _item(shape=PrimitiveShape.DOUBLE_RECT_V)
    top, bottom = item_v._blade_rects(QRectF(0, 0, 10.0, 4.0))
    assert top.top() == 0 and bottom.bottom() == pytest.approx(4.0)
    assert bottom.top() > top.bottom()


def test_double_rect_paints_without_error(view, qtbot):
    item = _item(shape=PrimitiveShape.DOUBLE_RECT_V)
    view.add_device_item("d1", item)
    view.grab()  # forces a paint pass


# --- z-ordering -----------------------------------------------------------


def test_labels_render_above_device_items(view):
    item = _item()
    view.add_device_item("d1", item)
    label = view.get_label_item("d1")
    assert label.zValue() > item.zValue()
