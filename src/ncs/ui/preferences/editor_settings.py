"""Editor settings plugin for code navigation preferences.

This module contains the EditorSettingsPlugin that allows users to
configure which code editor to use for "click to open" functionality
in the logging panel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ncs.plugins.settings_plugin import SettingsPlugin
from ncs.ui.preferences.manager import PreferencesManager
from ncs.utils.editor_launcher import CodeEditor

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


class EditorSettingsPlugin(SettingsPlugin):
    """Settings plugin for code editor configuration.

    Allows users to select which code editor to use when clicking on
    code locations in the logging panel:
    - VSCode: Uses vscode:// protocol
    - PyCharm: Uses jetbrains:// protocol (requires JetBrains Toolbox)

    Note: PyCharm support requires JetBrains Toolbox to be installed
    for the jetbrains:// URL protocol handler to work.
    """

    def __init__(self) -> None:
        """Initialize the editor settings plugin."""
        self._widget: QWidget | None = None
        self._editor_combo: QComboBox | None = None
        self._suppress_warning_check: QCheckBox | None = None

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "editor"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Editor"

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
        return 15  # Between appearance (0) and devices (20)

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget.

        Args:
            parent: Parent widget (the dialog).

        Returns:
            A QWidget containing the editor settings controls.
        """
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Editor selection group
        editor_group = QGroupBox("Code Editor")
        editor_layout = QFormLayout(editor_group)

        # Editor selector
        self._editor_combo = QComboBox()
        self._editor_combo.addItem("Visual Studio Code", CodeEditor.VSCODE.value)
        self._editor_combo.addItem("PyCharm", CodeEditor.PYCHARM.value)
        self._editor_combo.currentIndexChanged.connect(self._on_editor_changed)
        editor_layout.addRow("Preferred editor:", self._editor_combo)

        # Description
        desc = QLabel(
            "Select which editor to open when double-clicking on code "
            "locations in the Logging panel. The editor will open the "
            "file at the exact line number."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        editor_layout.addRow(desc)

        layout.addWidget(editor_group)

        # PyCharm options group
        pycharm_group = QGroupBox("PyCharm Options")
        pycharm_layout = QVBoxLayout(pycharm_group)

        self._suppress_warning_check = QCheckBox(
            "Suppress JetBrains Toolbox warning on startup"
        )
        pycharm_layout.addWidget(self._suppress_warning_check)

        # PyCharm requirements note
        note = QLabel(
            "<i>Note: PyCharm integration requires JetBrains Toolbox to be "
            "installed. Toolbox registers the jetbrains:// URL protocol "
            "that allows opening files directly in PyCharm.</i>"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        pycharm_layout.addWidget(note)

        layout.addWidget(pycharm_group)
        layout.addStretch()

        self._widget = widget
        self._update_pycharm_options_visibility()
        return widget

    def _on_editor_changed(self, index: int) -> None:
        """Handle editor selection change."""
        self._update_pycharm_options_visibility()

    def _update_pycharm_options_visibility(self) -> None:
        """Show/hide PyCharm-specific options based on selection."""
        if self._editor_combo is None:
            return

        is_pycharm = self._editor_combo.currentData() == CodeEditor.PYCHARM.value
        if self._suppress_warning_check is not None:
            self._suppress_warning_check.setEnabled(is_pycharm)

    def load_settings(self) -> None:
        """Load current settings into the widget.

        Populates the controls with current values from PreferencesManager.
        """
        if not self._editor_combo:
            return

        prefs = PreferencesManager.get_instance()

        # Load editor selection
        editor = prefs.get("code_editor", CodeEditor.VSCODE.value)
        index = self._editor_combo.findData(editor)
        if index >= 0:
            self._editor_combo.setCurrentIndex(index)

        # Load suppress warning setting
        if self._suppress_warning_check:
            suppress = prefs.get("suppress_jetbrains_warning", False)
            self._suppress_warning_check.setChecked(suppress)

        self._update_pycharm_options_visibility()

    def save_settings(self) -> None:
        """Save widget values to persistent storage.

        Writes the current control values to PreferencesManager.
        """
        if not self._editor_combo:
            return

        prefs = PreferencesManager.get_instance()

        # Save editor selection
        prefs.set("code_editor", self._editor_combo.currentData())

        # Save suppress warning setting
        if self._suppress_warning_check:
            prefs.set("suppress_jetbrains_warning", self._suppress_warning_check.isChecked())

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            List of error messages, or empty list if valid.
        """
        # No validation needed - all values are from combo boxes
        return []
