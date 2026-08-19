"""Routing tests: which control widget claims which device-tree item.

The mock backend's ``area_det`` (an ophyd-async ``SimBlobDetector``) exposes
no signal interface at all -- no get/put/set/subscribe/connected, only
async describe/read/stage/trigger. It must be claimed by
``SignalControlWidget`` (via ``is_signal_item``), not by
``CameraControlWidget`` (via ``is_area_detector``), even though it is
conceptually a detector.

Constructing a real ``ophyd_async`` ``SimBlobDetector`` here would pull in
the full ophyd-async device machinery just to check attribute shape, so a
minimal stand-in with the same attribute surface (no ``cam``, no
get/subscribe, class name ``SimBlobDetector``) is used instead -- the
matchers under test (``is_area_detector`` / ``is_signal_item``) only ever
look at attribute presence and class/name strings, never behavior.
"""
from __future__ import annotations

from lightfall.devices.model import DeviceCategory, DeviceInfo
from lightfall.devices.sim.areadetector import SimDetector
from lightfall.ui.models.device_tree import DeviceTreeItem, NodeType
from lightfall.ui.widgets.camera.base import is_area_detector
from lightfall.ui.widgets.signal_control import is_signal_item


class SimBlobDetector:
    """Minimal stand-in for the ophyd-async mock area detector.

    Mirrors the real device's attribute surface for matcher purposes only:
    no ``cam`` component, no ``get``/``subscribe``/``connected``, just the
    async lifecycle methods a Bluesky-pluggable ophyd-async device exposes.
    """

    def __init__(self, name: str = "area_det") -> None:
        self.name = name

    async def describe(self):
        return {}

    async def read(self):
        return {}

    async def stage(self):
        return None

    async def trigger(self):
        return None


def _device_item(ophyd_obj, category: DeviceCategory) -> DeviceTreeItem:
    info = DeviceInfo(name=ophyd_obj.name, category=category)
    return DeviceTreeItem(
        name=ophyd_obj.name,
        node_type=NodeType.DEVICE,
        ophyd_obj=ophyd_obj,
        device_info=info,
    )


def test_is_area_detector_claims_sync_sim_detector():
    """The synchronous ophyd SimDetector (has .cam) is a genuine area detector."""
    det = SimDetector(name="sim_det")
    item = _device_item(det, DeviceCategory.DETECTOR)
    assert is_area_detector(item) is True


def test_is_area_detector_does_not_claim_async_blob_detector():
    """The ophyd-async SimBlobDetector has no .cam and no signal interface --
    CameraControlWidget must not claim it."""
    det = SimBlobDetector(name="area_det")
    item = _device_item(det, DeviceCategory.DETECTOR)
    assert is_area_detector(item) is False


def test_is_signal_item_routes_async_blob_detector_to_signal_control():
    """area_det has no readable interface but also isn't an area detector
    by our markers, so it must fall through to SignalControlWidget via the
    plain DETECTOR category check."""
    det = SimBlobDetector(name="area_det")
    item = _device_item(det, DeviceCategory.DETECTOR)
    assert is_signal_item(item) is True


def test_is_signal_item_still_rejects_sync_sim_detector():
    """Sanity check: the real area detector is correctly excluded from
    signal control (it's claimed by CameraControlWidget instead)."""
    det = SimDetector(name="sim_det")
    item = _device_item(det, DeviceCategory.DETECTOR)
    assert is_signal_item(item) is False
