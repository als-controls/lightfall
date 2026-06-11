"""Built-in settings plugins for NCS.

This module contains the core settings plugins that are part of NCS,
including the AppearanceSettingsPlugin for theme and font settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.ui.theme import ThemeManager

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


class AppearanceSettingsPlugin(SettingsPlugin):
    """Built-in settings for theme and font appearance.

    This plugin provides controls for:
    - Theme selection (dynamically populated from ThemeRegistry)
    - Font size adjustment

    As a preload plugin (preload=True), it applies the saved theme
    immediately on load, before the main window is created.
    """

    def __init__(self) -> None:
        """Initialize the appearance settings plugin."""
        self._widget: QWidget | None = None
        self._theme_combo: QComboBox | None = None
        self._font_spin: QSpinBox | None = None
        self._islands_check: QCheckBox | None = None
        self._original_theme: str = "system"
        self._original_islands: bool = True
        self._original_font_size: int = 10

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
        theme_mgr = ThemeManager.get_instance()
        # Apply the islands-layout preference before the theme so the first
        # stylesheet generation already reflects it.
        theme_mgr.set_islands_mode(bool(prefs.get("islands_mode", False)))
        # Use set_theme_by_name for string-based themes
        theme_mgr.set_theme_by_name(prefs.theme)
        # Apply the saved base font size so it takes effect before any UI
        # appears (mirrors the theme being applied here at preload).
        self._apply_font_size(prefs.font_size)

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

        # Theme selector - populated dynamically from ThemeManager
        self._theme_combo = QComboBox()
        self._populate_theme_combo()
        self._theme_combo.currentIndexChanged.connect(self.apply_preview)
        appearance_layout.addRow("Theme:", self._theme_combo)

        # Font size
        font_layout = QHBoxLayout()
        self._font_spin = QSpinBox()
        self._font_spin.setRange(8, 24)
        self._font_spin.setSuffix(" pt")
        self._font_spin.valueChanged.connect(self.apply_preview)
        font_layout.addWidget(self._font_spin)
        font_layout.addStretch()
        appearance_layout.addRow("Font Size:", font_layout)

        # Console syntax style
        self._console_style_combo = QComboBox()
        self._console_style_combo.addItem("Auto (follows theme)", "")
        # Popular dark styles
        for style_name in [
            "monokai", "dracula", "one-dark", "nord", "gruvbox-dark",
            "native", "vim", "github-dark", "solarized-dark",
            # Light styles
            "default", "friendly", "tango", "solarized-light",
        ]:
            self._console_style_combo.addItem(style_name.title(), style_name)
        appearance_layout.addRow("Console Style:", self._console_style_combo)

        # Islands layout — applies the rounded floating-panel look on top of
        # any theme (independent of the theme's colors).
        self._islands_check = QCheckBox("Islands layout (rounded floating panels)")
        self._islands_check.toggled.connect(self.apply_preview)
        appearance_layout.addRow("Layout:", self._islands_check)

        layout.addWidget(appearance_group)
        layout.addStretch()

        self._widget = widget
        return widget

    def _populate_theme_combo(self) -> None:
        """Populate the theme combo box with available themes."""
        if not self._theme_combo:
            return

        self._theme_combo.clear()
        theme_mgr = ThemeManager.get_instance()

        for theme_info in theme_mgr.get_available_themes():
            self._theme_combo.addItem(
                theme_info["display_name"],
                theme_info["name"],
            )

    def load_settings(self) -> None:
        """Load current settings into the widget.

        Populates the controls with current values from PreferencesManager.
        """
        if not self._theme_combo or not self._font_spin:
            return

        prefs = PreferencesManager.get_instance()
        self._original_theme = prefs.theme
        self._original_islands = bool(prefs.get("islands_mode", False))
        self._original_font_size = prefs.font_size

        # Populating the controls below is initialization, not a user edit.
        # Block widget signals so these writes don't fire apply_preview(),
        # which would re-apply the already-active theme (a full stylesheet
        # regen + pyqtgraph re-theme) and stutter the dialog open. Worse, the
        # islands checkbox is set before the theme combo, so an un-blocked
        # toggle would apply the wrong (index-0) theme and then the right one.
        widgets = [
            w
            for w in (
                self._theme_combo,
                self._font_spin,
                self._islands_check,
                self._console_style_combo,
            )
            if w is not None
        ]
        for w in widgets:
            w.blockSignals(True)
        try:
            # Islands layout checkbox
            if self._islands_check is not None:
                self._islands_check.setChecked(self._original_islands)

            # Set theme combo by name
            index = self._theme_combo.findData(prefs.theme)
            if index >= 0:
                self._theme_combo.setCurrentIndex(index)
            else:
                # Theme not found, default to system
                index = self._theme_combo.findData("system")
                if index >= 0:
                    self._theme_combo.setCurrentIndex(index)

            # Set font size
            self._font_spin.setValue(prefs.font_size)

            # Set console style
            if self._console_style_combo:
                console_style = prefs.get("console_syntax_style", "")
                index = self._console_style_combo.findData(console_style)
                if index >= 0:
                    self._console_style_combo.setCurrentIndex(index)
        finally:
            for w in widgets:
                w.blockSignals(False)

    def save_settings(self) -> None:
        """Save widget values to persistent storage.

        Writes the current control values to PreferencesManager.
        """
        if not self._theme_combo or not self._font_spin:
            return

        prefs = PreferencesManager.get_instance()
        prefs.theme = self._theme_combo.currentData()
        prefs.font_size = self._font_spin.value()

        if self._console_style_combo:
            prefs.set("console_syntax_style", self._console_style_combo.currentData())

        if self._islands_check is not None:
            prefs.set("islands_mode", self._islands_check.isChecked())

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            Empty list (no validation needed for appearance settings).
        """
        return []

    def apply_preview(self) -> None:
        """Apply theme + font size temporarily for live preview.

        Called when the user changes the theme, islands, or font-size
        controls, allowing immediate visual feedback.
        """
        theme_mgr = ThemeManager.get_instance()

        # Islands layout (re-applies the stylesheet on change).
        if self._islands_check is not None:
            theme_mgr.set_islands_mode(self._islands_check.isChecked())

        if self._theme_combo:
            theme_name = self._theme_combo.currentData()
            if theme_name:
                theme_mgr.set_theme_by_name(theme_name)

        if self._font_spin is not None:
            self._apply_font_size(self._font_spin.value())

    def revert_preview(self) -> None:
        """Revert to the original theme + islands layout + font size on cancel.

        Restores what was active when the dialog opened.
        """
        theme_mgr = ThemeManager.get_instance()
        theme_mgr.set_islands_mode(self._original_islands)
        theme_mgr.set_theme_by_name(self._original_theme)
        self._apply_font_size(self._original_font_size)

    @staticmethod
    def _apply_font_size(size: int) -> None:
        """Set the application-wide base font point size.

        Qt propagates this to every widget that hasn't had an explicit font
        set, and relayouts automatically.
        """
        app = QApplication.instance()
        if app is None:
            return
        font = app.font()
        font.setPointSize(int(size))
        app.setFont(font)
