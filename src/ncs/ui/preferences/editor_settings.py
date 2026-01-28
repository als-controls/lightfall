"""External tools settings plugin for code navigation preferences.

This module contains the ExternalToolsSettingsPlugin that allows users to
configure which external code editor to use for "click to open" functionality
in the logging panel and other code navigation features.
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
from ncs.utils.editor_launcher import EDITOR_PROTOCOLS, CodeEditor, is_editor_available

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


class ExternalToolsSettingsPlugin(SettingsPlugin):
    """Settings plugin for external tools configuration.

    Allows users to select which external code editor to use when clicking on
    code locations in the logging panel and other code navigation features:
    - VSCode: Uses vscode://file/{path}:{line}:{column} protocol
    - PyCharm: Uses jetbrains://pycharm/navigate/reference?path={path}&line={line}
      (requires JetBrains Toolbox to register the jetbrains:// protocol)

    Note: Both editors must be installed and have their URL protocol handlers
    registered for the "open in editor" functionality to work.
    """

    def __init__(self) -> None:
        """Initialize the external tools settings plugin."""
        self._widget: QWidget | None = None
        self._editor_combo: QComboBox | None = None
        self._suppress_pycharm_warning_check: QCheckBox | None = None
        self._status_label: QLabel | None = None

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "external_tools"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "External Tools"

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
            A QWidget containing the external tools settings controls.
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

        # Status label showing if protocol is available
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        editor_layout.addRow("Status:", self._status_label)

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

        # URL Protocol Info group
        protocol_group = QGroupBox("URL Protocols")
        protocol_layout = QVBoxLayout(protocol_group)

        # Protocol format info
        protocol_info = QLabel(
            "<b>VSCode:</b> <code>vscode://file/{path}:{line}:{column}</code><br>"
            "<b>PyCharm:</b> <code>jetbrains://pycharm/navigate/reference?project={project}&amp;path={path}:{line}:{column}</code>"
        )
        protocol_info.setWordWrap(True)
        protocol_info.setTextFormat(protocol_info.textFormat())
        protocol_layout.addWidget(protocol_info)

        # Suppress PyCharm warning checkbox
        self._suppress_pycharm_warning_check = QCheckBox(
            "Suppress JetBrains Toolbox warning on startup"
        )
        protocol_layout.addWidget(self._suppress_pycharm_warning_check)

        # Requirements note
        note = QLabel(
            "<i>Note: The editor must be installed and have its URL protocol "
            "handler registered. VSCode registers its protocol automatically. "
            "PyCharm requires JetBrains Toolbox to be installed (Toolbox "
            "registers the jetbrains:// protocol handler).</i>"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        protocol_layout.addWidget(note)

        layout.addWidget(protocol_group)
        layout.addStretch()

        self._widget = widget
        self._update_status()
        return widget

    def _on_editor_changed(self, index: int) -> None:
        """Handle editor selection change."""
        self._update_status()

    def _update_status(self) -> None:
        """Update the status label based on selected editor."""
        if self._editor_combo is None or self._status_label is None:
            return

        editor_str = self._editor_combo.currentData()
        editor = CodeEditor(editor_str) if editor_str else CodeEditor.VSCODE
        protocol = EDITOR_PROTOCOLS.get(editor, editor.value)

        if is_editor_available(editor):
            self._status_label.setText(
                f'<span style="color: green;">✓ {protocol}:// protocol is registered</span>'
            )
        else:
            self._status_label.setText(
                f'<span style="color: orange;">⚠ {protocol}:// protocol not found</span>'
            )

        # Enable/disable suppress checkbox based on editor
        if self._suppress_pycharm_warning_check:
            is_pycharm = editor == CodeEditor.PYCHARM
            self._suppress_pycharm_warning_check.setEnabled(is_pycharm)

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
        if self._suppress_pycharm_warning_check:
            suppress = prefs.get("suppress_pycharm_warning", False)
            self._suppress_pycharm_warning_check.setChecked(suppress)

        self._update_status()

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
        if self._suppress_pycharm_warning_check:
            prefs.set("suppress_pycharm_warning", self._suppress_pycharm_warning_check.isChecked())

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            List of error messages, or empty list if valid.
        """
        # No validation needed - all values are from combo boxes
        return []
