"""IPC/NATS settings plugin.

Configures the connection to the NATS message broker for inter-process
communication, including topic prefix and trusted application management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.ui.preferences.manager import PreferencesManager

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


class IPCSettingsPlugin(SettingsPlugin):
    """Settings plugin for IPC/NATS configuration.

    Allows users to configure:
    - NATS server URL
    - Topic prefix
    - Trusted application management

    Preferences keys:
    - ``ipc_nats_url``: str — NATS broker URL
    - ``ipc_topic_prefix``: str — topic prefix for all published messages
    """

    def __init__(self) -> None:
        self._widget: QWidget | None = None
        self._url_edit: QLineEdit | None = None
        self._prefix_edit: QLineEdit | None = None
        self._status_label: QLabel | None = None
        self._trusted_list: QListWidget | None = None
        self._revoke_btn: QPushButton | None = None

    @property
    def name(self) -> str:
        return "ipc"

    @property
    def display_name(self) -> str:
        return "IPC"

    @property
    def icon(self) -> QIcon | None:
        return None

    @property
    def category(self) -> str:
        return "general"

    @property
    def priority(self) -> int:
        return 80

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- NATS Connection group ---
        connection_group = QGroupBox("NATS Connection")
        connection_layout = QFormLayout(connection_group)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("nats://broker.als.lbl.gov:4222")
        connection_layout.addRow("Server URL:", self._url_edit)

        self._prefix_edit = QLineEdit()
        self._prefix_edit.setPlaceholderText("als.7011")
        connection_layout.addRow("Topic Prefix:", self._prefix_edit)

        self._status_label = QLabel("Disconnected")
        connection_layout.addRow("Status:", self._status_label)

        layout.addWidget(connection_group)

        # --- Trusted Applications group ---
        trusted_group = QGroupBox("Trusted Applications")
        trusted_layout = QVBoxLayout(trusted_group)

        self._trusted_list = QListWidget()
        trusted_layout.addWidget(self._trusted_list)

        self._revoke_btn = QPushButton("Revoke Selected")
        self._revoke_btn.clicked.connect(self._on_revoke)
        trusted_layout.addWidget(self._revoke_btn)

        layout.addWidget(trusted_group)
        layout.addStretch()

        self._widget = widget
        return widget

    def _on_revoke(self) -> None:
        if not self._trusted_list:
            return
        selected = self._trusted_list.currentItem()
        if selected is not None:
            row = self._trusted_list.row(selected)
            self._trusted_list.takeItem(row)

    def load_settings(self) -> None:
        prefs = PreferencesManager.get_instance()

        if self._url_edit:
            self._url_edit.setText(prefs.get("ipc_nats_url", ""))
        if self._prefix_edit:
            self._prefix_edit.setText(prefs.get("ipc_topic_prefix", "als.7011"))

    def save_settings(self) -> None:
        prefs = PreferencesManager.get_instance()

        url = self._url_edit.text().strip() if self._url_edit else ""
        prefix = self._prefix_edit.text().strip() if self._prefix_edit else ""

        prefs.set("ipc_nats_url", url)
        prefs.set("ipc_topic_prefix", prefix)

    def validate(self) -> list[str]:
        errors: list[str] = []

        url = self._url_edit.text().strip() if self._url_edit else ""
        if url and not url.startswith("nats://"):
            errors.append("NATS server URL must start with 'nats://' (or leave empty to disable IPC)")

        return errors
