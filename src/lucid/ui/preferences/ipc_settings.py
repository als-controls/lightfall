"""IPC/NATS settings plugin.

Configures the connection to the NATS message broker for inter-process
communication, including topic prefix and trusted application management.
"""

from __future__ import annotations

import json
import logging
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.ui.preferences.manager import PreferencesManager

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon

    from lucid.ipc.trust import TrustManager


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
        self._test_btn: QPushButton | None = None
        self._status_label: QLabel | None = None
        self._trusted_list: QListWidget | None = None
        self._revoke_btn: QPushButton | None = None
        self._trust_manager: TrustManager | None = None

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

        test_layout = QHBoxLayout()
        self._test_btn = QPushButton("Test Connection")
        self._test_btn.clicked.connect(self._on_test_connection)
        test_layout.addWidget(self._test_btn)
        self._status_label = QLabel()
        test_layout.addWidget(self._status_label)
        test_layout.addStretch()
        connection_layout.addRow("", test_layout)

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

    def set_trust_manager(self, trust_manager: TrustManager) -> None:
        """Set the TrustManager reference so revoke() can be called."""
        self._trust_manager = trust_manager

    def _on_test_connection(self) -> None:
        """Test TCP connectivity to the configured NATS server."""
        if not self._status_label:
            return

        url = self._url_edit.text().strip() if self._url_edit else ""
        if not url:
            self._status_label.setText("No URL configured")
            self._status_label.setStyleSheet("color: orange;")
            return

        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 4222

        self._status_label.setText("Testing...")
        self._status_label.setStyleSheet("color: gray;")
        QCoreApplication.processEvents()

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((host, port))

            # NATS servers send "INFO {...}\r\n" immediately on connect
            data = sock.recv(4096).decode("utf-8", errors="replace")
            sock.close()

            if data.startswith("INFO "):
                info = json.loads(data[5:].strip())
                self._status_label.setText(f"Connected — NATS v{info.get('version', '?')}")
                self._status_label.setStyleSheet("color: green;")
            else:
                self._status_label.setText("Connected, but not a NATS server")
                self._status_label.setStyleSheet("color: orange;")

        except TimeoutError:
            self._status_label.setText("Connection timeout")
            self._status_label.setStyleSheet("color: red;")
        except ConnectionRefusedError:
            self._status_label.setText("Connection refused")
            self._status_label.setStyleSheet("color: red;")
        except socket.gaierror as e:
            self._status_label.setText(f"DNS error: {e}")
            self._status_label.setStyleSheet("color: red;")
        except Exception as e:
            self._status_label.setText(f"Error: {e}")
            self._status_label.setStyleSheet("color: red;")
            logger.error("NATS test connection error: %s", e)

    def _on_revoke(self) -> None:
        if not self._trusted_list:
            return
        selected = self._trusted_list.currentItem()
        if selected is not None:
            app_name = selected.text()
            row = self._trusted_list.row(selected)
            self._trusted_list.takeItem(row)
            if self._trust_manager:
                self._trust_manager.revoke(app_name)

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
