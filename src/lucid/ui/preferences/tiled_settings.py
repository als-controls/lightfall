"""Tiled data catalog settings plugin for NCS.

This module contains the TiledSettingsPlugin that allows users to
configure the Tiled server connection for data catalog integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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


@dataclass
class AccessOverride:
    """Time-windowed admin override of write-time ESAF selection."""

    esaf_id: str
    start: datetime
    end: datetime
    set_by: Optional[str] = None


def access_override_from_prefs(prefs: PreferencesManager) -> Optional[AccessOverride]:
    """Read AccessOverride from preferences, or None if any field missing."""
    esaf = prefs.get("tiled_access_override_esaf_id", "")
    start = prefs.get("tiled_access_override_start", "")
    end = prefs.get("tiled_access_override_end", "")
    set_by = prefs.get("tiled_access_override_set_by", None) or None
    if not (esaf and start and end):
        return None
    try:
        return AccessOverride(
            esaf_id=esaf,
            start=datetime.fromisoformat(start),
            end=datetime.fromisoformat(end),
            set_by=set_by,
        )
    except ValueError:
        return None


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
        self._beamline_edit: QLineEdit | None = None
        self._alshub_url_edit: QLineEdit | None = None
        self._alshub_api_key_edit: QLineEdit | None = None
        self._override_esaf_edit: QLineEdit | None = None
        self._override_start_edit: QLineEdit | None = None
        self._override_end_edit: QLineEdit | None = None

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
        self._url_edit.editingFinished.connect(self._validate_url)
        self._url_error_label = QLabel()
        self._url_error_label.setStyleSheet("color: #f44336; font-size: 9pt;")
        self._url_error_label.hide()
        connection_layout.addRow("Server URL:", self._url_edit)
        connection_layout.addRow("", self._url_error_label)

        # Authentication mode
        self._auth_mode_combo = QComboBox()
        self._auth_mode_combo.addItem("None", "none")
        self._auth_mode_combo.addItem("API Key", "api_key")
        self._auth_mode_combo.addItem("Keycloak (use LUCID session)", "keycloak")
        self._auth_mode_combo.currentIndexChanged.connect(self._on_auth_mode_changed)
        connection_layout.addRow("Authentication:", self._auth_mode_combo)

        # API Key (shown only when API Key auth is selected)
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText("(optional)")
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_label = QLabel("API Key:")
        connection_layout.addRow(self._api_key_label, self._api_key_edit)

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

        # Per-entry authorization (alshub-api integration)
        authz_group = QGroupBox("Per-entry authorization (alshub-api)")
        authz_layout = QFormLayout()

        self._beamline_edit = QLineEdit()
        self._beamline_edit.setPlaceholderText("4.0.2")
        authz_layout.addRow("Beamline:", self._beamline_edit)

        self._alshub_url_edit = QLineEdit()
        self._alshub_url_edit.setPlaceholderText("https://bcgmds01.als.lbl.gov")
        authz_layout.addRow("alshub URL:", self._alshub_url_edit)

        self._alshub_api_key_edit = QLineEdit()
        self._alshub_api_key_edit.setPlaceholderText("(API key)")
        self._alshub_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        authz_layout.addRow("alshub API key:", self._alshub_api_key_edit)

        authz_help = QLabel(
            "Set all three to enable AccessStamper. Each Tiled entry written "
            "after reconnect will be stamped with an access_blob containing "
            "the operator's Keycloak identity and the active ESAF for this "
            "beamline (looked up via alshub-api). Leave any field blank to "
            "disable stamping."
        )
        authz_help.setWordWrap(True)
        authz_help.setStyleSheet("color: gray;")
        authz_layout.addRow(authz_help)

        authz_group.setLayout(authz_layout)
        layout.addWidget(authz_group)

        # Admin: access override section (hidden for non-admin/staff users)
        override_group = QGroupBox("Admin: write-time access override")
        override_layout = QFormLayout()

        self._override_esaf_edit = QLineEdit()
        self._override_esaf_edit.setPlaceholderText("BLS-00480-001")
        override_layout.addRow("ESAF ID:", self._override_esaf_edit)

        self._override_start_edit = QLineEdit()
        self._override_start_edit.setPlaceholderText("2026-04-26T18:00:00+00:00")
        override_layout.addRow("Start (ISO8601):", self._override_start_edit)

        self._override_end_edit = QLineEdit()
        self._override_end_edit.setPlaceholderText("2026-04-27T02:00:00+00:00")
        override_layout.addRow("End (ISO8601):", self._override_end_edit)

        override_help = QLabel(
            "When set and current time is within [start, end], the AccessStamper "
            "will use this ESAF ID instead of querying alshub-api."
        )
        override_help.setWordWrap(True)
        override_help.setStyleSheet("color: gray;")
        override_layout.addRow(override_help)

        override_group.setLayout(override_layout)
        override_group.setVisible(self._user_has_admin_or_staff())
        layout.addWidget(override_group)

        layout.addStretch()

        self._widget = widget
        self._update_enabled_state()
        return widget

    def _user_has_admin_or_staff(self) -> bool:
        """Check current SessionManager token for admin/staff groups."""
        try:
            from lucid.auth.session import SessionManager

            session = SessionManager.get_instance().session
            if not session or not session.token:
                return False
            groups = getattr(session.token, "claims", {}).get("groups", []) or []
            if "tiled:admin" in groups:
                return True
            return any(g.startswith("staff:") for g in groups)
        except Exception:
            return False

    def _on_enabled_changed(self, state: int) -> None:
        """Handle enable checkbox state change."""
        self._update_enabled_state()

    def _on_auth_mode_changed(self, index: int) -> None:
        """Show/hide API key field based on auth mode."""
        self._update_enabled_state()

    def _update_enabled_state(self) -> None:
        """Update widget enabled states based on checkbox and auth mode."""
        enabled = self._enabled_check.isChecked() if self._enabled_check else False
        auth_mode = self._auth_mode_combo.currentData() if self._auth_mode_combo else "none"
        show_api_key = enabled and auth_mode == "api_key"

        if self._url_edit:
            self._url_edit.setEnabled(enabled)
        if self._auth_mode_combo:
            self._auth_mode_combo.setEnabled(enabled)
        if self._api_key_edit:
            self._api_key_edit.setEnabled(show_api_key)
            self._api_key_edit.setVisible(show_api_key)
        if self._api_key_label:
            self._api_key_label.setVisible(show_api_key)
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
        auth_mode = self._auth_mode_combo.currentData() if self._auth_mode_combo else "none"

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
            success, message = service.test_connection(url, api_key, auth_mode=auth_mode)

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
            self._enabled_check.setChecked(prefs.get("tiled_enabled", True))

        if self._url_edit:
            self._url_edit.setText(prefs.get("tiled_url", "http://bcgtiled.dhcp.lbl.gov:8000/"))

        if self._auth_mode_combo:
            auth_mode = prefs.get("tiled_auth_mode", "keycloak")
            index = self._auth_mode_combo.findData(auth_mode)
            if index >= 0:
                self._auth_mode_combo.setCurrentIndex(index)

        if self._api_key_edit:
            self._api_key_edit.setText(prefs.get("tiled_api_key", ""))

        if self._beamline_edit:
            self._beamline_edit.setText(prefs.get("tiled_beamline", ""))

        if self._alshub_url_edit:
            self._alshub_url_edit.setText(prefs.get("tiled_alshub_url", ""))

        if self._alshub_api_key_edit:
            self._alshub_api_key_edit.setText(prefs.get("tiled_alshub_api_key", ""))

        if self._status_label:
            self._status_label.setText("")

        if self._override_esaf_edit:
            self._override_esaf_edit.setText(prefs.get("tiled_access_override_esaf_id", ""))

        if self._override_start_edit:
            self._override_start_edit.setText(prefs.get("tiled_access_override_start", ""))

        if self._override_end_edit:
            self._override_end_edit.setText(prefs.get("tiled_access_override_end", ""))

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

        auth_mode = self._auth_mode_combo.currentData() if self._auth_mode_combo else "none"

        prefs.set("tiled_enabled", enabled)
        prefs.set("tiled_url", url)
        prefs.set("tiled_auth_mode", auth_mode)
        prefs.set("tiled_api_key", api_key or "")

        beamline = self._beamline_edit.text().strip() if self._beamline_edit else ""
        alshub_url = self._alshub_url_edit.text().strip() if self._alshub_url_edit else ""
        alshub_api_key = self._alshub_api_key_edit.text().strip() if self._alshub_api_key_edit else ""
        prefs.set("tiled_beamline", beamline)
        prefs.set("tiled_alshub_url", alshub_url)
        prefs.set("tiled_alshub_api_key", alshub_api_key)

        override_esaf = self._override_esaf_edit.text().strip() if self._override_esaf_edit else ""
        override_start = self._override_start_edit.text().strip() if self._override_start_edit else ""
        override_end = self._override_end_edit.text().strip() if self._override_end_edit else ""
        prefs.set("tiled_access_override_esaf_id", override_esaf)
        prefs.set("tiled_access_override_start", override_start)
        prefs.set("tiled_access_override_end", override_end)

        # Update the TiledService with new configuration
        try:
            from lucid.services.tiled_service import TiledService

            service = TiledService.get_instance()
            service.configure(
                url=url,
                api_key=api_key or None,
                enabled=enabled,
                auth_mode=auth_mode,
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
            elif not self._is_valid_url(url):
                errors.append("Tiled server URL must start with http:// or https://")

        return errors

    @staticmethod
    def _is_valid_url(url: str) -> bool:
        """Check if URL is valid and uses http(s) scheme."""
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
            return parsed.scheme in ("http", "https") and bool(parsed.netloc)
        except Exception:
            return False

    def _validate_url(self) -> None:
        """Validate URL field and show/hide inline error."""
        url = self._url_edit.text().strip() if self._url_edit else ""
        if not url:
            self._url_error_label.hide()
            return
        if self._is_valid_url(url):
            self._url_error_label.hide()
        else:
            self._url_error_label.setText("URL must start with http:// or https://")
            self._url_error_label.show()
