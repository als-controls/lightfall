"""IPC/NATS settings plugin.

Configures the connection to the NATS message broker for inter-process
communication, including topic prefix and trusted application management.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QCheckBox,
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

from lightfall.ipc.local_server import (
    nats_binary_version,
    probe_nats,
    resolve_nats_binary,
)
from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.manager import PreferencesManager

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon

    from lightfall.ipc.trust import TrustManager


class IPCSettingsPlugin(SettingsPlugin):
    """Settings plugin for IPC/NATS configuration.

    Allows users to configure:
    - NATS server URL
    - Topic prefix
    - Trusted application management

    Preferences keys:
    - ``ipc_nats_url``: str — NATS broker URL
    - ``ipc_topic_prefix``: str — topic prefix for all published messages
    - ``ipc_display_name``: str — human-readable name for this Lightfall instance
    - ``ipc_use_local_nats``: bool — run a bundled local nats-server instead of the site broker
    - ``ipc_local_nats_port``: int — port for the local nats-server (default 4222)
    """

    def __init__(self) -> None:
        self._widget: QWidget | None = None
        self._url_edit: QLineEdit | None = None
        self._prefix_edit: QLineEdit | None = None
        self._display_name_edit: QLineEdit | None = None
        self._test_btn: QPushButton | None = None
        self._status_label: QLabel | None = None
        self._trusted_list: QListWidget | None = None
        self._revoke_btn: QPushButton | None = None
        self._trust_manager: TrustManager | None = None
        self._local_enable_cb: QCheckBox | None = None
        self._local_port_edit: QLineEdit | None = None
        self._local_status_label: QLabel | None = None

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

        # --- Local NATS Server group ---
        local_group = QGroupBox("Local NATS Server")
        local_layout = QFormLayout(local_group)

        self._local_enable_cb = QCheckBox("Run a local NATS server (instead of the site broker)")
        self._local_enable_cb.toggled.connect(self._on_local_toggled)
        local_layout.addRow("", self._local_enable_cb)

        self._local_port_edit = QLineEdit()
        self._local_port_edit.setPlaceholderText("4222")
        local_layout.addRow("Port:", self._local_port_edit)

        self._local_status_label = QLabel()
        local_layout.addRow("Binary:", self._local_status_label)

        layout.addWidget(local_group)

        # --- NATS Connection group ---
        connection_group = QGroupBox("NATS Connection")
        connection_layout = QFormLayout(connection_group)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("nats://broker.als.lbl.gov:4222")
        connection_layout.addRow("Server URL:", self._url_edit)

        self._prefix_edit = QLineEdit()
        self._prefix_edit.setPlaceholderText("als.7011")
        connection_layout.addRow("Topic Prefix:", self._prefix_edit)

        self._display_name_edit = QLineEdit()
        self._display_name_edit.setPlaceholderText("e.g. CMS Hutch")
        connection_layout.addRow("Display Name:", self._display_name_edit)

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
        self._trusted_list.setSelectionBehavior(
            QListWidget.SelectionBehavior.SelectRows
        )
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

    def _on_local_toggled(self, checked: bool) -> None:
        """Grey out the Server URL field when the local server is enabled."""
        if self._url_edit is not None:
            self._url_edit.setEnabled(not checked)

    def _refresh_binary_status(self) -> None:
        """Detect the nats-server binary and gate the local-server option on it.

        The option is only available when a ``nats-server`` executable is found
        (bundled via the optional ``local-nats`` extra, or installed on PATH).
        When absent, the checkbox is disabled and force-unchecked so the panel
        falls back to the site broker.
        """
        if self._local_status_label is None:
            return
        path = resolve_nats_binary()
        available = path is not None

        if self._local_enable_cb is not None:
            self._local_enable_cb.setEnabled(available)
            if not available and self._local_enable_cb.isChecked():
                # Drop a stale "use local" pref when the binary has gone away;
                # this re-enables the Server URL field via _on_local_toggled.
                self._local_enable_cb.setChecked(False)
        if self._local_port_edit is not None:
            self._local_port_edit.setEnabled(available)

        if not available:
            self._local_status_label.setText(
                "nats-server not found — install it or `pip install lightfall[local-nats]`"
            )
            self._local_status_label.setStyleSheet("color: gray;")
            return

        version = nats_binary_version(path)
        ver = f"v{version}" if version else "(version unknown)"
        self._local_status_label.setText(f"{ver} — {path}")
        self._local_status_label.setStyleSheet("color: green;")

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

        info = probe_nats(host, port, timeout=5.0)
        if info is not None:
            self._status_label.setText(f"Connected — NATS v{info.get('version', '?')}")
            self._status_label.setStyleSheet("color: green;")
        else:
            self._status_label.setText("Could not reach a NATS server")
            self._status_label.setStyleSheet("color: red;")

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
            self._url_edit.setText(prefs.get("ipc_nats_url", "nats://bcgnats.als.private.lbl.gov:4222"))
        if self._prefix_edit:
            self._prefix_edit.setText(prefs.get("ipc_topic_prefix", "als.7011"))
        if self._display_name_edit:
            self._display_name_edit.setText(prefs.get("ipc_display_name", ""))
        if self._local_enable_cb:
            use_local = bool(prefs.get("ipc_use_local_nats", False))
            self._local_enable_cb.setChecked(use_local)
            self._on_local_toggled(use_local)
        if self._local_port_edit:
            self._local_port_edit.setText(str(prefs.get("ipc_local_nats_port", 4222)))
        self._refresh_binary_status()

    def save_settings(self) -> None:
        prefs = PreferencesManager.get_instance()

        url = self._url_edit.text().strip() if self._url_edit else ""
        prefix = self._prefix_edit.text().strip() if self._prefix_edit else ""
        display_name = self._display_name_edit.text().strip() if self._display_name_edit else ""

        prefs.set("ipc_nats_url", url)
        prefs.set("ipc_topic_prefix", prefix)
        prefs.set("ipc_display_name", display_name)
        use_local = self._local_enable_cb.isChecked() if self._local_enable_cb else False
        prefs.set("ipc_use_local_nats", use_local)
        port_text = self._local_port_edit.text().strip() if self._local_port_edit else ""
        try:
            port = int(port_text) if port_text else 4222
        except ValueError:
            port = 4222
        prefs.set("ipc_local_nats_port", port)

    def validate(self) -> list[str]:
        errors: list[str] = []

        use_local = self._local_enable_cb.isChecked() if self._local_enable_cb else False

        if use_local:
            port_text = self._local_port_edit.text().strip() if self._local_port_edit else ""
            try:
                port = int(port_text) if port_text else 4222
            except ValueError:
                errors.append("Local NATS port must be a number")
            else:
                if not (1 <= port <= 65535):
                    errors.append("Local NATS port must be between 1 and 65535")
            if resolve_nats_binary() is None:
                errors.append("nats-server not found (install it or `pip install lightfall[local-nats]`)")
        else:
            url = self._url_edit.text().strip() if self._url_edit else ""
            if url and not url.startswith("nats://"):
                errors.append("NATS server URL must start with 'nats://' (or leave empty to disable IPC)")

        return errors
