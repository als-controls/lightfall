"""Visualization settings plugin.

Provides user preferences for visualization behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lightfall_settings import SettingSpec

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.form.schema import (
    Choice,
    Integer,
    Section,
    SettingField,
    Toggle,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtGui import QIcon

_VISUALIZATION_TYPES = ["table", "plot_1d", "heatmap", "scatter", "image_stack", "volume"]
_COLORMAPS = ["viridis", "plasma", "inferno", "magma", "cividis", "gray", "hot", "cool"]

_SCOPES = frozenset({"user", "branch", "beamline", "facility"})


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

    def specs(self) -> Sequence[SettingSpec]:
        return [
            SettingSpec(key="visualization.auto_select", scopes=_SCOPES,
                        label="Automatically select visualization type"),
            SettingSpec(key="visualization.default_type", scopes=_SCOPES, label="Default type"),
            SettingSpec(key="visualization.decimation_threshold", scopes=_SCOPES,
                        label="Decimation threshold"),
            SettingSpec(key="visualization.update_rate", scopes=_SCOPES, label="Max update rate"),
            SettingSpec(key="visualization.colormap", scopes=_SCOPES, label="Default colormap"),
            SettingSpec(key="visualization.show_grid", scopes=_SCOPES, label="Show grid on plots"),
            SettingSpec(key="visualization.show_legend", scopes=_SCOPES, label="Show legend on plots"),
        ]

    def sections(self) -> Sequence[Section]:
        return [
            Section("Visualization Selection", [
                SettingField("visualization.auto_select", Toggle(),
                             label="Automatically select visualization type"),
                SettingField("visualization.default_type",
                             Choice([(t, t) for t in _VISUALIZATION_TYPES]), label="Default type"),
            ]),
            Section("Performance", [
                SettingField("visualization.decimation_threshold", Integer(100, 100000, step=1000),
                             label="Decimation threshold", unit="points"),
                SettingField("visualization.update_rate", Integer(5, 60),
                             label="Max update rate", unit="Hz"),
            ]),
            Section("Appearance", [
                SettingField("visualization.colormap", Choice([(c, c) for c in _COLORMAPS]),
                             label="Default colormap"),
                SettingField("visualization.show_grid", Toggle(), label="Show grid on plots"),
                SettingField("visualization.show_legend", Toggle(), label="Show legend on plots"),
            ]),
        ]
