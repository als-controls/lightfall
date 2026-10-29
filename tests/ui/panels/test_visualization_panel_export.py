"""VisualizationPanel Export... button writes the current viz's data."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from PySide6.QtWidgets import QFileDialog, QMessageBox

from lightfall.ui.panels.visualization_panel import VisualizationPanel
from lightfall.visualization.widgets.plot_1d import Plot1DVisualization


class _FakeViz(Plot1DVisualization):
    def export_columns(self):
        return {"x": np.array([1.0, 2.0]), "y": np.array([3.0, 4.0])}


def _panel(qtbot) -> VisualizationPanel:
    panel = VisualizationPanel()
    qtbot.addWidget(panel)
    return panel


def test_export_writes_file_and_appends_extension(qtbot, tmp_path, monkeypatch) -> None:
    panel = _panel(qtbot)
    panel._current_widget = _FakeViz()
    panel._entry = SimpleNamespace(
        metadata={"start": {"scan_id": 7, "sample_name": "Hall probe"}}
    )
    seen = {}

    def fake_dialog(parent, caption, default, filters):
        seen["default"] = default
        return str(tmp_path / "out"), "CSV Files (*.csv)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_dialog)
    panel._on_export_clicked()

    assert seen["default"] == "scan7_Hall_probe.csv"
    assert (tmp_path / "out.csv").read_text().splitlines() == ["x,y", "1.0,3.0", "2.0,4.0"]


def test_export_without_run_tells_user(qtbot, monkeypatch) -> None:
    panel = _panel(qtbot)
    panel._current_widget = None
    called = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: called.append(a))
    panel._on_export_clicked()
    assert called
