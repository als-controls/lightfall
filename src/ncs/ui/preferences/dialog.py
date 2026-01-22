"""Preferences dialog for NCS.

Provides a dialog for viewing and editing user preferences.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ncs.ui.preferences.manager import PreferencesManager
from ncs.ui.theme import Theme, ThemeManager

if TYPE_CHECKING:
    pass


class PreferencesDialog(QDialog):
    """
    Dialog for editing user preferences.

    Provides controls for:
    - Theme selection (Light/Dark/System)
    - Font size adjustment
    - UI visibility options

    Example:
        >>> dialog = PreferencesDialog(parent)
        >>> if dialog.exec() == QDialog.DialogCode.Accepted:
        ...     # Preferences were applied
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the preferences dialog.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self._prefs_manager = PreferencesManager.get_instance()
        self._theme_manager = ThemeManager.get_instance()

        self._setup_ui()
        self._load_current_values()

    def _setup_ui(self) -> None:
        """Setup the dialog UI."""
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Appearance group
        appearance_group = QGroupBox("Appearance")
        appearance_layout = QFormLayout(appearance_group)

        # Theme selector
        self._theme_combo = QComboBox()
        self._theme_combo.addItem("Light", Theme.LIGHT.value)
        self._theme_combo.addItem("Dark", Theme.DARK.value)
        self._theme_combo.addItem("System", Theme.SYSTEM.value)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        appearance_layout.addRow("Theme:", self._theme_combo)

        # Font size
        font_layout = QHBoxLayout()
        self._font_spin = QSpinBox()
        self._font_spin.setRange(8, 24)
        self._font_spin.setSuffix(" pt")
        self._font_spin.valueChanged.connect(self._on_font_size_changed)
        font_layout.addWidget(self._font_spin)
        font_layout.addStretch()
        appearance_layout.addRow("Font Size:", font_layout)

        layout.addWidget(appearance_group)

        # Add stretch to push buttons to bottom
        layout.addStretch()

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_current_values(self) -> None:
        """Load current preference values into controls."""
        # Theme
        current_theme = self._prefs_manager.theme
        index = self._theme_combo.findData(current_theme)
        if index >= 0:
            self._theme_combo.setCurrentIndex(index)

        # Font size
        self._font_spin.setValue(self._prefs_manager.font_size)

    def _on_theme_changed(self, index: int) -> None:
        """Handle theme selection change - apply immediately for preview."""
        theme_value = self._theme_combo.itemData(index)
        if theme_value:
            try:
                theme = Theme(theme_value)
                self._theme_manager.set_theme(theme)
            except ValueError:
                pass

    def _on_font_size_changed(self, value: int) -> None:
        """Handle font size change - apply immediately for preview."""
        # Font size changes are applied on accept
        pass

    def accept(self) -> None:
        """Save preferences and close dialog."""
        # Save theme
        theme_value = self._theme_combo.currentData()
        if theme_value:
            self._prefs_manager.theme = theme_value

        # Save font size
        self._prefs_manager.font_size = self._font_spin.value()

        super().accept()
