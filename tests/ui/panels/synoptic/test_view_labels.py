"""Tests for synoptic device labels."""
import pytest

from lightfall.ui.panels.synoptic.items import Device2DItem
from lightfall.ui.panels.synoptic.models import DeviceSynopticData, ViewPreset
from lightfall.ui.panels.synoptic.view import SynopticView


@pytest.fixture
def view(qtbot):
    v = SynopticView()
    qtbot.addWidget(v)
    return v


def _add(view, device_id="d1", name="mono_energy", **synoptic_kwargs):
    data = DeviceSynopticData(position=(9.0, 0.0, 0.0), **synoptic_kwargs)
    item = Device2DItem(device_id, name, data)
    view.add_device_item(device_id, item)
    return item


def test_label_created_with_device(view):
    _add(view)
    label = view.get_label_item("d1")
    assert label is not None
    assert label.toPlainText() == "mono_energy"


def test_label_text_override(view):
    _add(view, label_text="Mono (eV)")
    assert view.get_label_item("d1").toPlainText() == "Mono (eV)"


def test_labels_visibility_toggle(view):
    _add(view)
    view.set_labels_visible(False)
    assert not view.get_label_item("d1").isVisible()
    view.set_labels_visible(True)
    assert view.get_label_item("d1").isVisible()


def test_label_removed_with_device(view):
    _add(view)
    view.remove_device_item("d1")
    assert view.get_label_item("d1") is None


def test_label_repositions_on_preset_change(view):
    # Offset is +Z: visible in SIDE (x-z) projection, zero in TOP (x-y).
    _add(view, label_offset=(0.0, 0.0, 0.35))
    pos_side = view.get_label_item("d1").pos()
    assert pos_side.y() == pytest.approx(0.35)
    view.apply_view_preset(ViewPreset.TOP)
    pos_top = view.get_label_item("d1").pos()
    assert pos_top.y() == pytest.approx(0.0)


def test_hidden_device_item_and_label_start_hidden(view):
    data = DeviceSynopticData(position=(9.0, 0.0, 0.0), visible=False)
    item = Device2DItem("dh", "hidden_dev", data)
    view.add_device_item("dh", item)
    assert not item.isVisible()
    assert not view.get_label_item("dh").isVisible()
