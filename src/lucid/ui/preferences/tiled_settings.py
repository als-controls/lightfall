"""Tiled data catalog settings plugin for NCS.

This module contains the TiledSettingsPlugin that allows users to
configure the Tiled server connection for data catalog integration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.ui.preferences.manager import PreferencesManager
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


class TiledSettingsPlugin(SettingsPlugin):
    """Settings plugin for Tiled data catalog configuration.

    Allows users to configure:
    - Enable/disable Tiled integration
    - Tiled server URL
    - API key for authentication
    - Test connection functionality

    Note: Changes to the Tiled configuration are applied when saved
    and take effect on next connection (or immediately if reconnecting).
    """

    def __init__(self) -> None:
        """Initialize the Tiled settings plugin."""
        self._widget: QWidget | None = None
        self._enabled_check: QCheckBox | None = None
        self._url_edit: QLineEdit | None = None
        self._api_key_edit: QLineEdit | None = None
        self._test_button: QPushButton | None = None
        self._status_label: QLabel | None = None

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "tiled"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Tiled Data Catalog"

    @property
    def icon(self) -> QIcon | None:
        """Return optional icon for sidebar."""
        return None

    @property
    def category(self) -> str:
        """Return category for grouping."""
        return "data"

    @property
    def priority(self) -> int:
        """Return sort order (lower = higher in list)."""
        return 50

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget.

        Args:
            parent: Parent widget (the dialog).

        Returns:
            A QWidget containing the Tiled settings controls.
        """
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Connection group
        connection_group = QGroupBox("Tiled Connection")
        connection_layout = QFormLayout(connection_group)

        # Enable checkbox
        self._enabled_check = QCheckBox("Enable Tiled integration")
        self._enabled_check.stateChanged.connect(self._on_enabled_changed)
        connection_layout.addRow(self._enabled_check)

        # Server URL
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("http://localhost:8000")
        connection_layout.addRow("Server URL:", self._url_edit)

        # API Key
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText("(optional)")
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        connection_layout.addRow("API Key:", self._api_key_edit)

        # Test connection button and status
        test_layout = QHBoxLayout()
        self._test_button = QPushButton("Test Connection")
        self._test_button.clicked.connect(self._on_test_connection)
        test_layout.addWidget(self._test_button)

        self._status_label = QLabel()
        test_layout.addWidget(self._status_label)
        test_layout.addStretch()

        connection_layout.addRow(test_layout)

        # Description
        desc = QLabel(
            "Tiled provides a data catalog for storing and accessing "
            "bluesky run data. When enabled, acquisition data is automatically "
            "streamed to the Tiled server."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        connection_layout.addRow(desc)

        layout.addWidget(connection_group)
        layout.addStretch()

        self._widget = widget
        self._update_enabled_state()
        return widget

    def _on_enabled_changed(self, state: int) -> None:
        """Handle enable checkbox state change."""
        self._update_enabled_state()

    def _update_enabled_state(self) -> None:
        """Update widget enabled states based on checkbox."""
        enabled = self._enabled_check.isChecked() if self._enabled_check else False
        if self._url_edit:
            self._url_edit.setEnabled(enabled)
        if self._api_key_edit:
            self._api_key_edit.setEnabled(enabled)
        if self._test_button:
            self._test_button.setEnabled(enabled)

    def _on_test_connection(self) -> None:
        """Handle test connection button click."""
        if not self._url_edit or not self._status_label:
            return

        url = self._url_edit.text().strip()
        api_key = self._api_key_edit.text() if self._api_key_edit else None
        if api_key:
            api_key = api_key.strip() or None

        if not url:
            self._status_label.setText("Enter a URL first")
            self._status_label.setStyleSheet("color: orange;")
            return

        self._status_label.setText("Testing...")
        self._status_label.setStyleSheet("color: gray;")

        # Force UI update
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.processEvents()

        try:
            from lucid.services.tiled_service import TiledService

            service = TiledService.get_instance()
            success, message = service.test_connection(url, api_key)

            if success:
                self._status_label.setText("Connected!")
                self._status_label.setStyleSheet("color: green;")
            else:
                self._status_label.setText(message)
                self._status_label.setStyleSheet("color: red;")

        except Exception as e:
            self._status_label.setText(f"Error: {e}")
            self._status_label.setStyleSheet("color: red;")
            logger.error("Test connection error: {}", e)

    def load_settings(self) -> None:
        """Load current settings into the widget.

        Populates the controls with current values from PreferencesManager.
        """
        prefs = PreferencesManager.get_instance()

        if self._enabled_check:
            self._enabled_check.setChecked(prefs.get("tiled_enabled", False))

        if self._url_edit:
            self._url_edit.setText(prefs.get("tiled_url", ""))

        if self._api_key_edit:
            self._api_key_edit.setText(prefs.get("tiled_api_key", ""))

        if self._status_label:
            self._status_label.setText("")

        self._update_enabled_state()

    def save_settings(self) -> None:
        """Save widget values to persistent storage.

        Writes the current control values to PreferencesManager and
        updates the TiledService configuration.
        """
        prefs = PreferencesManager.get_instance()

        enabled = self._enabled_check.isChecked() if self._enabled_check else False
        url = self._url_edit.text().strip() if self._url_edit else ""
        api_key = self._api_key_edit.text().strip() if self._api_key_edit else ""

        prefs.set("tiled_enabled", enabled)
        prefs.set("tiled_url", url)
        prefs.set("tiled_api_key", api_key or "")

        # Update the TiledService with new configuration
        try:
            from lucid.services.tiled_service import TiledService

            service = TiledService.get_instance()
            service.configure(
                url=url,
                api_key=api_key or None,
                enabled=enabled,
            )

            # Connect if enabled (async to avoid blocking UI)
            if enabled and url:
                service.connect_async()
            else:
                service.disconnect()

        except Exception as e:
            logger.error("Failed to update TiledService: {}", e)

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            List of error messages, or empty list if valid.
        """
        errors = []

        enabled = self._enabled_check.isChecked() if self._enabled_check else False
        if enabled:
            url = self._url_edit.text().strip() if self._url_edit else ""
            if not url:
                errors.append("Tiled server URL is required when enabled")

        return errors
