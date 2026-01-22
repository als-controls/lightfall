"""Built-in settings plugins for NCS.

This module contains the core settings plugins that are part of NCS,
including the AppearanceSettingsPlugin for theme and font settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ncs.plugins.settings_plugin import SettingsPlugin
from ncs.ui.preferences.manager import PreferencesManager
from ncs.ui.theme import Theme, ThemeManager

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


class AppearanceSettingsPlugin(SettingsPlugin):
    """Built-in settings for theme and font appearance.

    This plugin provides controls for:
    - Theme selection (Light/Dark/System)
    - Font size adjustment

    As a preload plugin (preload=True), it applies the saved theme
    immediately on load, before the main window is created.
    """

    def __init__(self) -> None:
        """Initialize the appearance settings plugin."""
        self._widget: QWidget | None = None
        self._theme_combo: QComboBox | None = None
        self._font_spin: QSpinBox | None = None
        self._original_theme: str = "system"

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "appearance"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Appearance"

    @property
    def icon(self) -> QIcon | None:
        """Return optional icon for sidebar."""
        return None

    @property
    def category(self) -> str:
        """Return category for grouping."""
        return "general"

    @property
    def priority(self) -> int:
        """Return sort order (lower = higher in list)."""
        return 0  # First in list

    def on_loaded(self) -> None:
        """Apply saved theme immediately on load.

        Called for preload plugins before the main window is created.
        This ensures the correct theme is applied before any UI appears.
        """
        prefs = PreferencesManager.get_instance()
        theme_value = prefs.theme
        try:
            theme = Theme(theme_value)
            ThemeManager.get_instance().set_theme(theme)
        except ValueError:
            # Invalid theme value, use default
            pass

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget.

        Args:
            parent: Parent widget (the dialog).

        Returns:
            A QWidget containing the appearance settings controls.
        """
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Appearance group
        appearance_group = QGroupBox("Appearance")
        appearance_layout = QFormLayout(appearance_group)

        # Theme selector
        self._theme_combo = QComboBox()
        self._theme_combo.addItem("Light", Theme.LIGHT.value)
        self._theme_combo.addItem("Dark", Theme.DARK.value)
        self._theme_combo.addItem("System", Theme.SYSTEM.value)
        self._theme_combo.currentIndexChanged.connect(self.apply_preview)
        appearance_layout.addRow("Theme:", self._theme_combo)

        # Font size
        font_layout = QHBoxLayout()
        self._font_spin = QSpinBox()
        self._font_spin.setRange(8, 24)
        self._font_spin.setSuffix(" pt")
        font_layout.addWidget(self._font_spin)
        font_layout.addStretch()
        appearance_layout.addRow("Font Size:", font_layout)

        layout.addWidget(appearance_group)
        layout.addStretch()

        self._widget = widget
        return widget

    def load_settings(self) -> None:
        """Load current settings into the widget.

        Populates the controls with current values from PreferencesManager.
        """
        if not self._theme_combo or not self._font_spin:
            return

        prefs = PreferencesManager.get_instance()
        self._original_theme = prefs.theme

        # Set theme combo
        index = self._theme_combo.findData(prefs.theme)
        if index >= 0:
            self._theme_combo.setCurrentIndex(index)

        # Set font size
        self._font_spin.setValue(prefs.font_size)

    def save_settings(self) -> None:
        """Save widget values to persistent storage.

        Writes the current control values to PreferencesManager.
        """
        if not self._theme_combo or not self._font_spin:
            return

        prefs = PreferencesManager.get_instance()
        prefs.theme = self._theme_combo.currentData()
        prefs.font_size = self._font_spin.value()

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            Empty list (no validation needed for appearance settings).
        """
        return []

    def apply_preview(self) -> None:
        """Apply theme temporarily for live preview.

        Called when the user changes the theme selection, allowing
        immediate visual feedback.
        """
        if not self._theme_combo:
            return

        theme_value = self._theme_combo.currentData()
        if theme_value:
            try:
                theme = Theme(theme_value)
                ThemeManager.get_instance().set_theme(theme)
            except ValueError:
                pass

    def revert_preview(self) -> None:
        """Revert to the original theme if user cancels.

        Restores the theme that was active when the dialog opened.
        """
        try:
            theme = Theme(self._original_theme)
            ThemeManager.get_instance().set_theme(theme)
        except ValueError:
            pass
