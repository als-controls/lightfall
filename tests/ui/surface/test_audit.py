"""Audit findings: unnamed, ambiguous, undescribed."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QDockWidget,
    QMainWindow,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.surface.audit import audit_window, report


class _Gauge(QWidget):
    def paintEvent(self, event):  # noqa: N802 - Qt override
        QPainter(self).drawEllipse(self.rect().adjusted(2, 2, -2, -2))


def test_audit_findings(qtbot):
    w = QMainWindow()
    w.setWindowTitle("Lightfall")
    qtbot.addWidget(w)
    body = QWidget()
    lay = QVBoxLayout(body)
    lay.addWidget(QToolButton())            # icon-only, no name
    lay.addWidget(QPushButton("Run"))
    lay.addWidget(QPushButton("Run"))       # same name twice in one panel
    gauge = _Gauge()
    gauge.setMinimumSize(40, 40)
    lay.addWidget(gauge)                    # custom painted, undescribed
    dock = QDockWidget("Plans", w)
    dock.setWidget(body)
    w.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
    w.show()
    qtbot.waitExposed(w)

    kinds = {(f.kind, f.panel, f.role) for f in audit_window(w)}
    assert ("unnamed", "Plans", "button") in kinds
    assert ("ambiguous", "Plans", "button") in kinds
    assert any(k[0] == "undescribed" for k in kinds)
    text = report({"Plans": audit_window(w)}, {"broken": "RuntimeError: x"})
    assert "| unnamed | Plans | button |" in text and "broken: RuntimeError" in text
