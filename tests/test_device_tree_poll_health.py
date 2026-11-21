"""Device tree value poll: reports top-level read health, leaves collapsed children alone.

- A top-level device whose own read (readback / position / signal-like get)
  raises is reported to the catalog as a read failure; a good read as success.
- A device with nothing to read at the top level (e.g. an area detector, or the
  PI-MTE) reports nothing.
- Child signals are only read for expanded devices, so the poll doesn't read
  hardware nobody is looking at.
"""

from __future__ import annotations

from concurrent.futures import Future
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from ophyd import Component as Cpt
from ophyd import Device, Signal
from PySide6.QtWidgets import QApplication

from lightfall.devices.model import DeviceInfo
from lightfall.ui.models.device_tree import DeviceTreeItem, DeviceTreeModel, NodeType


class CountingSignal(Signal):
    """Signal that counts reads and can be made to fail."""

    def __init__(self, *args, fail=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.reads = 0
        self.fail = fail

    def get(self, **kwargs):
        self.reads += 1
        if self.fail:
            raise RuntimeError("No PICam camera found (serial='02062413')")
        return super().get(**kwargs)


class WithReadback(Device):
    readback = Cpt(CountingSignal, value=1.0)
    setpoint = Cpt(CountingSignal, value=1.0)


class DetectorLike(Device):
    """No readback, no position: nothing to read at the top level."""

    exposure = Cpt(CountingSignal, value=0.1)
    gain = Cpt(CountingSignal, value=1)


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def model(qapp):
    catalog = MagicMock()
    catalog.get_all_devices.return_value = []
    with patch.object(DeviceTreeModel, "_poll_value_refresh"):
        m = DeviceTreeModel(catalog)
        m._value_timer.stop()
    yield m, catalog
    m._value_timer.stop()
    m._value_pool.shutdown(wait=False)


def _add(model, name, ophyd_obj):
    info = MagicMock(spec=DeviceInfo)
    info.id = uuid4()
    info.name = name
    info.metadata = {}
    item = DeviceTreeItem(name, NodeType.DEVICE, model._root, ophyd_obj=ophyd_obj, device_info=info)
    for comp_name in ophyd_obj.component_names if hasattr(ophyd_obj, "component_names") else ():
        comp = getattr(ophyd_obj, comp_name)
        item.append_child(DeviceTreeItem(comp_name, NodeType.SIGNAL, item, ophyd_obj=comp))
    model._root.append_child(item)
    return item, info


def _done(result):
    f = Future()
    f.set_result(result)
    return f


def test_failing_top_level_readback_is_reported(model):
    m, catalog = model
    dev = WithReadback(name="dev")
    dev.readback.fail = True
    _, info = _add(m, "dev", dev)

    outcomes = m._fetch_all_values(expanded=set())
    assert len(outcomes) == 1
    device_id, ok, error = outcomes[0]
    assert device_id == str(info.id) and ok is False and "PICam" in error

    m._report_read_outcomes(_done(outcomes))
    catalog.record_read_failure.assert_called_once_with(str(info.id), error)
    catalog.record_read_success.assert_not_called()


def test_good_top_level_read_is_reported_as_success(model):
    m, catalog = model
    _, info = _add(m, "dev", WithReadback(name="dev"))
    m._report_read_outcomes(_done(m._fetch_all_values(expanded=set())))
    catalog.record_read_success.assert_called_once_with(str(info.id))
    catalog.record_read_failure.assert_not_called()


def test_signal_like_top_level_device_is_reported(model):
    m, _ = model
    sig = CountingSignal(name="sig", fail=True)
    _add(m, "sig", sig)
    ((_, ok, _),) = m._fetch_all_values(expanded=set())
    assert ok is False


def test_device_without_top_level_readback_reports_nothing_and_children_not_polled(model):
    m, catalog = model
    det = DetectorLike(name="det")
    _add(m, "det", det)

    outcomes = m._fetch_all_values(expanded=set())  # collapsed
    assert outcomes == []
    assert det.exposure.reads == 0 and det.gain.reads == 0
    m._report_read_outcomes(_done(outcomes))
    catalog.record_read_failure.assert_not_called()
    catalog.record_read_success.assert_not_called()


def test_children_polled_only_when_expanded(model):
    m, _ = model
    collapsed = DetectorLike(name="a")
    opened = DetectorLike(name="b")
    _add(m, "a", collapsed)
    _add(m, "b", opened)

    m._fetch_all_values(expanded={1})
    assert collapsed.exposure.reads == 0
    assert opened.exposure.reads == 1 and opened.gain.reads == 1


def test_failing_child_signal_is_not_a_device_failure(model):
    m, _ = model
    dev = WithReadback(name="dev")
    dev.setpoint.fail = True
    _add(m, "dev", dev)
    ((_, ok, _),) = m._fetch_all_values(expanded={0})
    assert ok is True


def test_no_provider_reads_all_children(model):
    m, _ = model
    det = DetectorLike(name="det")
    _add(m, "det", det)
    m._fetch_all_values(expanded=None)
    assert det.exposure.reads == 1


def test_catalog_without_read_health_api_is_tolerated(model):
    m, _ = model
    m._catalog = object()
    m._report_read_outcomes(_done([("x", False, "e")]))  # no exception
