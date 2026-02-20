"""Logbook backend settings plugin.

Configures the connection to the lucid-logbook backend service
for experiment logbook persistence and sync.
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


class LogbookSettingsPlugin(SettingsPlugin):
    """Settings plugin for logbook backend configuration.

    Allows users to configure:
    - Enable/disable logbook backend sync
    - Backend server URL
    - Test connection functionality
    - Offline mode behavior

    Preferences keys:
    - ``logbook_enabled``: bool — whether backend sync is active
    - ``logbook_url``: str — base URL of the lucid-logbook service
    - ``logbook_offline_only``: bool — force offline-only mode (local SQLite only)
    """

    def __init__(self) -> None:
        self._widget: QWidget | None = None
        self._enabled_check: QCheckBox | None = None
        self._url_edit: QLineEdit | None = None
        self._offline_check: QCheckBox | None = None
        self._test_button: QPushButton | None = None
        self._status_label: QLabel | None = None

    @property
    def name(self) -> str:
        return "logbook"

    @property
    def display_name(self) -> str:
        return "Logbook"

    @property
    def icon(self) -> QIcon | None:
        return None

    @property
    def category(self) -> str:
        return "data"

    @property
    def priority(self) -> int:
        return 55

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Connection group ---
        connection_group = QGroupBox("Logbook Backend")
        connection_layout = QFormLayout(connection_group)

        self._enabled_check = QCheckBox("Enable logbook backend sync")
        self._enabled_check.stateChanged.connect(self._on_enabled_changed)
        connection_layout.addRow(self._enabled_check)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("http://bcglucidlogbook.dhcp.lbl.gov")
        connection_layout.addRow("Server URL:", self._url_edit)

        # Test connection
        test_layout = QHBoxLayout()
        self._test_button = QPushButton("Test Connection")
        self._test_button.clicked.connect(self._on_test_connection)
        test_layout.addWidget(self._test_button)

        self._status_label = QLabel()
        test_layout.addWidget(self._status_label)
        test_layout.addStretch()
        connection_layout.addRow(test_layout)

        desc = QLabel(
            "The logbook backend (lucid-logbook) stores experiment notes "
            "and system-generated fragments. When disabled or unreachable, "
            "data is stored locally and synced when the connection is restored."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        connection_layout.addRow(desc)

        layout.addWidget(connection_group)

        # --- Offline group ---
        offline_group = QGroupBox("Offline Mode")
        offline_layout = QFormLayout(offline_group)

        self._offline_check = QCheckBox("Force offline-only mode (no server sync)")
        offline_layout.addRow(self._offline_check)

        offline_desc = QLabel(
            "When checked, the logbook uses only local SQLite storage "
            "and never attempts to sync with the backend. Useful for "
            "standalone or disconnected operation."
        )
        offline_desc.setWordWrap(True)
        offline_desc.setStyleSheet("color: gray;")
        offline_layout.addRow(offline_desc)

        layout.addWidget(offline_group)
        layout.addStretch()

        self._widget = widget
        self._update_enabled_state()
        return widget

    def _on_enabled_changed(self, _state: int) -> None:
        self._update_enabled_state()

    def _update_enabled_state(self) -> None:
        enabled = self._enabled_check.isChecked() if self._enabled_check else False
        if self._url_edit:
            self._url_edit.setEnabled(enabled)
        if self._test_button:
            self._test_button.setEnabled(enabled)

    def _on_test_connection(self) -> None:
        if not self._url_edit or not self._status_label:
            return

        url = self._url_edit.text().strip()
        if not url:
            self._status_label.setText("Enter a URL first")
            self._status_label.setStyleSheet("color: orange;")
            return

        self._status_label.setText("Testing...")
        self._status_label.setStyleSheet("color: gray;")

        from PySide6.QtCore import QCoreApplication

        QCoreApplication.processEvents()

        try:
            import httpx

            # Use proxy settings if configured
            client_kwargs: dict = {"timeout": 5.0}
            try:
                from lucid.ui.preferences.proxy_settings import ProxySettingsProvider
                proxy_url = ProxySettingsProvider.should_use_proxy_for_url(url)
                if proxy_url:
                    client_kwargs["proxy"] = proxy_url
            except Exception:
                pass

            with httpx.Client(**client_kwargs) as client:
                resp = client.get(f"{url.rstrip('/')}/health")
                if resp.status_code == 200:
                    # 401/403 means server is up but needs auth — still a success
                    self._status_label.setText("Server reachable ✓")
                    self._status_label.setStyleSheet("color: green;")
                else:
                    self._status_label.setText(f"HTTP {resp.status_code}")
                    self._status_label.setStyleSheet("color: red;")
        except Exception as e:
            self._status_label.setText(f"Unreachable: {e}")
            self._status_label.setStyleSheet("color: red;")
            logger.debug("Logbook test connection failed: {}", e)

    def load_settings(self) -> None:
        prefs = PreferencesManager.get_instance()

        if self._enabled_check:
            self._enabled_check.setChecked(prefs.get("logbook_enabled", False))
        if self._url_edit:
            self._url_edit.setText(prefs.get("logbook_url", "http://bcglucidlogbook.dhcp.lbl.gov"))
        if self._offline_check:
            self._offline_check.setChecked(prefs.get("logbook_offline_only", False))
        if self._status_label:
            self._status_label.setText("")

        self._update_enabled_state()

    def save_settings(self) -> None:
        prefs = PreferencesManager.get_instance()

        enabled = self._enabled_check.isChecked() if self._enabled_check else False
        url = self._url_edit.text().strip() if self._url_edit else ""
        offline_only = self._offline_check.isChecked() if self._offline_check else False

        prefs.set("logbook_enabled", enabled)
        prefs.set("logbook_url", url)
        prefs.set("logbook_offline_only", offline_only)

        logger.info(
            "Logbook settings saved: enabled={}, url={}, offline_only={}",
            enabled,
            url,
            offline_only,
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        enabled = self._enabled_check.isChecked() if self._enabled_check else False
        offline = self._offline_check.isChecked() if self._offline_check else False

        if enabled and not offline:
            url = self._url_edit.text().strip() if self._url_edit else ""
            if not url:
                errors.append("Logbook server URL is required when sync is enabled")

        return errors
