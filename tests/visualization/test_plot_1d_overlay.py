"""Plot1D checkable field list: overlay several fields on one plot."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt

from lightfall.visualization.widgets.plot_1d import Plot1DVisualization

from .document_generators import fake_run_from_pair, load_fixture

EVENTS = {
    "motor": np.array([0.0, 1.0, 2.0]),
    "motor_setpoint": np.array([0.0, 1.0, 2.0]),
    "det": np.array([5.0, 6.0, 7.0]),
}


def _widget(qtbot, monkeypatch) -> Plot1DVisualization:
    widget = Plot1DVisualization()
    qtbot.addWidget(widget)
    monkeypatch.setattr(widget, "_read_events_table", lambda: EVENTS)
    widget.set_run(fake_run_from_pair(load_fixture("1d_scalar_scan")))
    widget.set_stream("primary")
    return widget


def _curve_names(widget: Plot1DVisualization) -> list[str]:
    return [c.name() for c in widget._plot_widget.getPlotItem().listDataItems()]


def _item(widget: Plot1DVisualization, name: str):
    (item,) = widget._field_list.findItems(name, Qt.MatchFlag.MatchExactly)
    return item


def test_default_plots_single_first_field(qtbot, monkeypatch) -> None:
    widget = _widget(qtbot, monkeypatch)
    first = widget.get_fields()[0]
    assert widget.checked_fields() == [first]
    assert _curve_names(widget) == [first]
    assert widget._field_list.count() == len(widget.get_fields())


def test_checking_boxes_overlays_curves(qtbot, monkeypatch) -> None:
    widget = _widget(qtbot, monkeypatch)
    for name in widget.get_fields():
        _item(widget, name).setCheckState(Qt.CheckState.Checked)
    assert sorted(_curve_names(widget)) == sorted(widget.get_fields())

    _item(widget, "det").setCheckState(Qt.CheckState.Unchecked)
    assert "det" not in _curve_names(widget)
    assert widget._field_name in widget.checked_fields()


def test_set_field_resets_to_one_curve(qtbot, monkeypatch) -> None:
    widget = _widget(qtbot, monkeypatch)
    widget.set_checked_fields(widget.get_fields())
    widget.set_field("det")
    assert _curve_names(widget) == ["det"]
    assert _item(widget, "det").checkState() == Qt.CheckState.Checked


def test_refresh_redraws_all_checked(qtbot, monkeypatch) -> None:
    widget = _widget(qtbot, monkeypatch)
    widget.set_checked_fields(["det", "motor_setpoint"])
    widget.refresh()
    assert sorted(_curve_names(widget)) == ["det", "motor_setpoint"]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_csv_contains_x_and_checked_fields(qtbot, monkeypatch) -> None:
    widget = _widget(qtbot, monkeypatch)
    widget._x_combo.setCurrentText("motor")
    widget.set_checked_fields(["det", "motor_setpoint"])
    lines = widget.export_data("csv").decode().splitlines()
    assert lines[0].split(",")[0] == "motor"
    assert set(lines[0].split(",")[1:]) == {"det", "motor_setpoint"}
    assert len(lines) == 1 + len(EVENTS["det"])


def test_export_json_round_trips(qtbot, monkeypatch) -> None:
    import json

    widget = _widget(qtbot, monkeypatch)
    widget._x_combo.setCurrentText("motor")
    widget.set_checked_fields(["det"])
    payload = json.loads(widget.export_data("json"))
    assert payload == {"motor": [0.0, 1.0, 2.0], "det": [5.0, 6.0, 7.0]}


def test_export_with_nothing_checked_raises(qtbot, monkeypatch) -> None:
    import pytest

    widget = _widget(qtbot, monkeypatch)
    widget.set_checked_fields([])
    with pytest.raises(ValueError):
        widget.export_data("csv")


def test_base_default_export_skips_array_columns(monkeypatch) -> None:
    import xarray as xr

    from lightfall.visualization import base_visualization as bv
    from lightfall.visualization.widgets.table import TableVisualization

    ds = xr.Dataset({
        "det": ("time", np.array([1.0, 2.0])),
        "image": (("time", "y", "x"), np.zeros((2, 3, 3))),
    })
    monkeypatch.setattr("lightfall.utils.tiled_helpers.read_events", lambda s, **k: ds)
    widget = TableVisualization.__new__(TableVisualization)
    bv.BaseVisualization.__init__(widget)
    widget._run = {"primary": object()}
    widget._stream_name = "primary"
    assert list(bv.BaseVisualization.export_columns(widget)) == ["det"]
