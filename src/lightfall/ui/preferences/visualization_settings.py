"""Visualization settings plugin.

Provides user preferences for visualization behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.manager import PreferencesManager


class VisualizationSettingsPlugin(SettingsPlugin):
    """Settings plugin for visualization preferences."""

    @property
    def name(self) -> str:
        return "visualization"

    @property
    def display_name(self) -> str:
        return "Visualization"

    @property
    def icon(self) -> QIcon | None:
        return None

    @property
    def category(self) -> str:
        return "data"

    @property
    def priority(self) -> int:
        return 30

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget."""
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)

        # Selection settings
        selection_group = QGroupBox("Visualization Selection")
        selection_layout = QFormLayout(selection_group)

        self._auto_select = QCheckBox("Automatically select visualization type")
        selection_layout.addRow(self._auto_select)

        self._default_viz = QComboBox()
        self._default_viz.addItems([
            "table", "plot_1d", "heatmap", "scatter",
            "image_stack", "volume"
        ])
        selection_layout.addRow("Default type:", self._default_viz)

        layout.addWidget(selection_group)

        # Performance settings
        perf_group = QGroupBox("Performance")
        perf_layout = QFormLayout(perf_group)

        self._decimation_threshold = QSpinBox()
        self._decimation_threshold.setRange(100, 100000)
        self._decimation_threshold.setSingleStep(1000)
        self._decimation_threshold.setSuffix(" points")
        perf_layout.addRow("Decimation threshold:", self._decimation_threshold)

        self._update_rate = QSpinBox()
        self._update_rate.setRange(5, 60)
        self._update_rate.setSuffix(" Hz")
        perf_layout.addRow("Max update rate:", self._update_rate)

        layout.addWidget(perf_group)

        # Appearance settings
        appearance_group = QGroupBox("Appearance")
        appearance_layout = QFormLayout(appearance_group)

        self._default_colormap = QComboBox()
        self._default_colormap.addItems([
            "viridis", "plasma", "inferno", "magma",
            "cividis", "gray", "hot", "cool"
        ])
        appearance_layout.addRow("Default colormap:", self._default_colormap)

        self._show_grid = QCheckBox("Show grid on plots")
        appearance_layout.addRow(self._show_grid)

        self._show_legend = QCheckBox("Show legend on plots")
        appearance_layout.addRow(self._show_legend)

        layout.addWidget(appearance_group)

        layout.addStretch()

        return widget

    def load_settings(self) -> None:
        """Load settings from preferences."""
        prefs = PreferencesManager.get_instance()

        self._auto_select.setChecked(
            prefs.get("visualization.auto_select", True)
        )
        self._default_viz.setCurrentText(
            prefs.get("visualization.default_type", "plot_1d")
        )
        self._decimation_threshold.setValue(
            prefs.get("visualization.decimation_threshold", 10000)
        )
        self._update_rate.setValue(
            prefs.get("visualization.update_rate", 20)
        )
        self._default_colormap.setCurrentText(
            prefs.get("visualization.colormap", "viridis")
        )
        self._show_grid.setChecked(
            prefs.get("visualization.show_grid", True)
        )
        self._show_legend.setChecked(
            prefs.get("visualization.show_legend", True)
        )

    def save_settings(self) -> None:
        """Save settings to preferences."""
        prefs = PreferencesManager.get_instance()

        prefs.set("visualization.auto_select", self._auto_select.isChecked())
        prefs.set("visualization.default_type", self._default_viz.currentText())
        prefs.set("visualization.decimation_threshold", self._decimation_threshold.value())
        prefs.set("visualization.update_rate", self._update_rate.value())
        prefs.set("visualization.colormap", self._default_colormap.currentText())
        prefs.set("visualization.show_grid", self._show_grid.isChecked())
        prefs.set("visualization.show_legend", self._show_legend.isChecked())

    def validate(self) -> list[str]:
        """Validate settings."""
        return []

    def apply_preview(self) -> None:
        """Apply preview of settings."""
        pass

    def revert_preview(self) -> None:
        """Revert preview changes."""
        pass
