"""Visualizations describe themselves to the agent UI surface."""

import numpy as np
from PySide6.QtWidgets import QVBoxLayout, QWidget

from lightfall.ui.surface.snapshot import snapshot


def test_plot_1d_describes_series_and_hides_pyqtgraph_internals(qtbot):
    from lightfall.visualization.widgets.plot_1d import Plot1DVisualization

    host = QWidget()
    host.setWindowTitle("Lightfall")
    qtbot.addWidget(host)
    viz = Plot1DVisualization()
    QVBoxLayout(host).addWidget(viz)
    viz._plot_widget.plot(np.array([1.0, 2.0, 3.0]), np.array([10.0, 30.0, 20.0]),
                          name="DetectorDiodeCurrent")
    host.show()
    qtbot.waitExposed(host)

    desc = viz.agent_describe()
    assert desc["opaque"] is True
    assert desc["series"][0] == {"name": "DetectorDiodeCurrent", "points": 3,
                                 "x": [1.0, 3.0], "y": [10.0, 30.0]}
    text = snapshot()
    plot_line = next(ln for ln in text.splitlines() if ln.strip().startswith('plot "1D Plot"'))
    assert '"points": 3' in plot_line
    # Nothing from inside the pyqtgraph widget leaks into the outline.
    assert "GraphicsView" not in text and "unnamed" not in text.split(plot_line)[1]
