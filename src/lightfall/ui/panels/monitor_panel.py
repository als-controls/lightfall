"""Monitor panel: per-run, severity-coded log of monitor observations, with
a 'Discuss in assistant' hand-off to the reactive Claude agent."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lightfall.monitor.models import Observation
from lightfall.monitor.service import MonitorService
from lightfall.ui.panels.base import BasePanel, PanelMetadata

_SEVERITY_COLOR = {"info": "#6b7280", "warn": "#d97706", "critical": "#dc2626"}


def format_observation(obs: Observation) -> str:
    rec = f"  ·  {obs.recommendation}" if obs.recommendation else ""
    return f"[{obs.severity.upper()}] {obs.title} — {obs.message}{rec}"


class MonitorPanel(BasePanel):
    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall.panels.monitor",
        name="Monitor",
        description="Proactive feedback about the running measurement.",
        icon="activity",
        category="Data",
        default_area="right",
        proactive_init=False,  # stay lazy until opened
    )

    def _setup_ui(self) -> None:
        self._rows = QVBoxLayout()
        self._rows.addStretch(1)
        container = QWidget()
        container.setLayout(self._rows)
        self._layout.addWidget(container)

        svc = MonitorService.get_instance()
        for obs in svc.recent_observations():
            self.add_observation(obs)
        svc.observation.connect(self.add_observation)

    def add_observation(self, obs: Observation) -> None:
        svc = MonitorService.get_instance()
        row = QFrame()
        row.setObjectName("monitorRow")
        hl = QHBoxLayout(row)
        label = QLabel(format_observation(obs))
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {_SEVERITY_COLOR.get(obs.severity, '#6b7280')};")
        hl.addWidget(label, 1)
        btn = QPushButton("Discuss in assistant")
        btn.clicked.connect(lambda _checked=False, o=obs: svc.discuss_observation(o))
        hl.addWidget(btn, 0)
        # Insert above the trailing stretch.
        self._rows.insertWidget(self._rows.count() - 1, row)

    def row_count(self) -> int:
        # Number of observation rows (excludes the trailing stretch item).
        return max(0, self._rows.count() - 1)
