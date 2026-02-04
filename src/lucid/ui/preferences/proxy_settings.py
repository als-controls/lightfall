"""Network proxy settings plugin for NCS.

This module contains the ProxySettingsPlugin that allows users to
configure SOCKS/HTTP proxy settings for network connections.

The proxy is DISABLED by default - users must explicitly enable it.
Previously, proxy was auto-detected for *.lbl.gov URLs.
"""

from __future__ import annotations

import os
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.ui.preferences.manager import PreferencesManager
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


# Proxy type options
PROXY_TYPES = {
    "socks5": "SOCKS5",
    "socks4": "SOCKS4",
    "http": "HTTP",
}

# Default proxy configuration
DEFAULT_PROXY_HOST = "localhost"
DEFAULT_PROXY_PORT = 1080


class ProxySettingsProvider:
    """Provides proxy configuration from settings.

    This helper class provides a simple interface for components to
    retrieve proxy configuration. Used by KeycloakConfig, ALSBeamStatusService,
    and other network clients.

    The proxy is disabled by default. Users must explicitly enable it
    in Settings > Network Proxy.
    """

    @staticmethod
    def is_enabled() -> bool:
        """Check if proxy is enabled in settings.

        Returns:
            True if proxy is enabled.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("proxy_enabled", False)

    @staticmethod
    def get_proxy_type() -> str:
        """Get the configured proxy type.

        Returns:
            Proxy type string (socks5, socks4, or http).
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("proxy_type", "socks5")

    @staticmethod
    def get_proxy_host() -> str:
        """Get the configured proxy host.

        Returns:
            Proxy host string.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("proxy_host", DEFAULT_PROXY_HOST)

    @staticmethod
    def get_proxy_port() -> int:
        """Get the configured proxy port.

        Returns:
            Proxy port number.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("proxy_port", DEFAULT_PROXY_PORT)

    @staticmethod
    def is_auto_detect_enabled() -> bool:
        """Check if auto-detect for *.lbl.gov is enabled.

        Returns:
            True if auto-detect is enabled.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("proxy_auto_detect", False)

    @staticmethod
    def get_proxy_url() -> str | None:
        """Get the full proxy URL if proxy is enabled.

        Returns:
            Proxy URL (e.g., socks5://localhost:1080) or None if disabled.
        """
        if not ProxySettingsProvider.is_enabled():
            return None

        proxy_type = ProxySettingsProvider.get_proxy_type()
        host = ProxySettingsProvider.get_proxy_host()
        port = ProxySettingsProvider.get_proxy_port()

        return f"{proxy_type}://{host}:{port}"

    @staticmethod
    def should_use_proxy_for_url(url: str) -> str | None:
        """Determine if proxy should be used for a given URL.

        This method checks:
        1. If proxy is globally enabled, return proxy URL
        2. If auto-detect is enabled and URL is *.lbl.gov, return proxy URL
        3. Otherwise return None

        Args:
            url: The URL to check.

        Returns:
            Proxy URL to use, or None if no proxy needed.
        """
        # If globally enabled, use proxy for everything
        if ProxySettingsProvider.is_enabled():
            return ProxySettingsProvider.get_proxy_url()

        # Check auto-detect for *.lbl.gov
        if ProxySettingsProvider.is_auto_detect_enabled():
            try:
                parsed = urlparse(url)
                if parsed.hostname and parsed.hostname.endswith(".lbl.gov"):
                    # Auto-detect enabled - use configured proxy for lbl.gov
                    proxy_type = ProxySettingsProvider.get_proxy_type()
                    host = ProxySettingsProvider.get_proxy_host()
                    port = ProxySettingsProvider.get_proxy_port()
                    return f"{proxy_type}://{host}:{port}"
            except Exception:
                pass

        return None

    @staticmethod
    def configure_webengine_proxy() -> None:
        """Configure WebEngine proxy via environment variable.

        This MUST be called before any QWebEngineView is created, as
        WebEngine reads QTWEBENGINE_CHROMIUM_FLAGS only once at startup.

        Typically called from ProxySettingsPlugin.on_loaded() which runs
        during plugin preload, before the main window is created.
        """
        proxy_url = ProxySettingsProvider.get_proxy_url()

        if proxy_url:
            # Parse proxy URL to extract components
            parsed = urlparse(proxy_url)
            proxy_type = parsed.scheme or "socks5"
            host = parsed.hostname or DEFAULT_PROXY_HOST
            port = parsed.port or DEFAULT_PROXY_PORT

            # Build Chromium proxy flag
            # For SOCKS5: --proxy-server=socks5://host:port
            # For HTTP: --proxy-server=http://host:port
            proxy_arg = f"--proxy-server={proxy_type}://{host}:{port}"

            # Get existing flags and append
            existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
            if proxy_arg not in existing:
                if existing:
                    new_flags = f"{existing} {proxy_arg}"
                else:
                    new_flags = proxy_arg
                os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = new_flags
                logger.debug("Configured WebEngine proxy: {}", proxy_arg)
        else:
            logger.debug("WebEngine proxy not configured (proxy disabled)")


class ProxySettingsPlugin(SettingsPlugin):
    """Settings plugin for network proxy configuration.

    Allows users to configure:
    - Enable/disable proxy (master toggle, default: disabled)
    - Proxy type (socks5, socks4, http)
    - Proxy host and port
    - Auto-detect for *.lbl.gov URLs
    - Test connection functionality

    This plugin has preload=True to configure WebEngine proxy before
    any QWebEngineView is created.
    """

    def __init__(self) -> None:
        """Initialize the proxy settings plugin."""
        self._widget: QWidget | None = None
        # Proxy Configuration
        self._enabled_checkbox: QCheckBox | None = None
        self._type_combo: QComboBox | None = None
        self._host_edit: QLineEdit | None = None
        self._port_spin: QSpinBox | None = None
        self._auto_detect_checkbox: QCheckBox | None = None
        self._test_button: QPushButton | None = None
        self._status_label: QLabel | None = None

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "proxy"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Network Proxy"

    @property
    def icon(self) -> QIcon | None:
        """Return optional icon for sidebar."""
        return None

    @property
    def category(self) -> str:
        """Return category for grouping."""
        return "network"

    @property
    def priority(self) -> int:
        """Return sort order (lower = higher in list)."""
        return 25  # After appearance (10), before login (30)

    def on_loaded(self) -> None:
        """Called when plugin is loaded.

        Configure WebEngine proxy early, before any WebEngine views are created.
        This is critical because QTWEBENGINE_CHROMIUM_FLAGS is only read once.
        """
        ProxySettingsProvider.configure_webengine_proxy()
        logger.debug("ProxySettingsPlugin loaded, WebEngine proxy configured")

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget.

        Args:
            parent: Parent widget (the dialog).

        Returns:
            A QWidget containing the proxy settings controls.
        """
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Proxy Configuration group
        proxy_group = self._create_proxy_group()
        layout.addWidget(proxy_group)

        # Info section
        info_group = self._create_info_group()
        layout.addWidget(info_group)

        layout.addStretch()

        self._widget = widget
        self._update_enabled_state()
        return widget

    def _create_proxy_group(self) -> QGroupBox:
        """Create the Proxy Configuration group box.

        Returns:
            The configured QGroupBox.
        """
        group = QGroupBox("Proxy Configuration")
        layout = QFormLayout(group)

        # Enable checkbox (master toggle)
        self._enabled_checkbox = QCheckBox("Enable proxy for all connections")
        self._enabled_checkbox.setToolTip(
            "When enabled, all network connections will use the configured proxy.\n"
            "This is disabled by default."
        )
        self._enabled_checkbox.stateChanged.connect(self._update_enabled_state)
        layout.addRow(self._enabled_checkbox)

        # Proxy type selector
        self._type_combo = QComboBox()
        for key, label in PROXY_TYPES.items():
            self._type_combo.addItem(label, key)
        self._type_combo.setToolTip("Protocol type for the proxy connection")
        layout.addRow("Proxy Type:", self._type_combo)

        # Host field
        self._host_edit = QLineEdit()
        self._host_edit.setPlaceholderText(DEFAULT_PROXY_HOST)
        self._host_edit.setToolTip("Proxy server hostname or IP address")
        layout.addRow("Host:", self._host_edit)

        # Port spinner
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(DEFAULT_PROXY_PORT)
        self._port_spin.setToolTip("Proxy server port number")
        layout.addRow("Port:", self._port_spin)

        # Auto-detect checkbox
        self._auto_detect_checkbox = QCheckBox("Auto-enable proxy for *.lbl.gov URLs")
        self._auto_detect_checkbox.setToolTip(
            "When enabled, the proxy will be automatically used for URLs\n"
            "ending in .lbl.gov, even if the main proxy toggle is off.\n"
            "Useful for accessing LBNL internal services via SSH tunnel."
        )
        layout.addRow(self._auto_detect_checkbox)

        # Test connection button and status
        test_layout = QHBoxLayout()
        self._test_button = QPushButton("Test Connection")
        self._test_button.clicked.connect(self._on_test_connection)
        test_layout.addWidget(self._test_button)

        self._status_label = QLabel()
        test_layout.addWidget(self._status_label)
        test_layout.addStretch()

        layout.addRow(test_layout)

        return group

    def _create_info_group(self) -> QGroupBox:
        """Create the information group box.

        Returns:
            The configured QGroupBox.
        """
        group = QGroupBox("Information")
        layout = QVBoxLayout(group)

        info_text = QLabel(
            "<b>When to use a proxy:</b><br>"
            "If you need to access LBNL internal services (*.lbl.gov) from outside "
            "the network, you can use an SSH tunnel as a SOCKS5 proxy.<br><br>"
            "<b>Setting up an SSH tunnel:</b><br>"
            "<code>ssh -D 1080 -N username@remote.lbl.gov</code><br><br>"
            "This creates a SOCKS5 proxy on localhost:1080 that tunnels through "
            "the remote server.<br><br>"
            "<b>Note:</b> The proxy setting affects Keycloak authentication, "
            "ALS beam status, and embedded browser (WebEngine) connections."
        )
        info_text.setWordWrap(True)
        info_text.setOpenExternalLinks(True)
        info_text.setStyleSheet("color: #666; font-size: 9pt;")
        layout.addWidget(info_text)

        return group

    def _update_enabled_state(self) -> None:
        """Update the enabled state of proxy configuration fields."""
        if not self._enabled_checkbox:
            return

        enabled = self._enabled_checkbox.isChecked()
        auto_detect = self._auto_detect_checkbox.isChecked() if self._auto_detect_checkbox else False

        # Fields are enabled if main toggle is on OR auto-detect is on
        fields_enabled = enabled or auto_detect

        if self._type_combo:
            self._type_combo.setEnabled(fields_enabled)
        if self._host_edit:
            self._host_edit.setEnabled(fields_enabled)
        if self._port_spin:
            self._port_spin.setEnabled(fields_enabled)
        if self._test_button:
            self._test_button.setEnabled(fields_enabled)

    def _on_test_connection(self) -> None:
        """Handle test connection button click."""
        if not self._status_label:
            return

        # Get current settings from UI
        host = self._host_edit.text().strip() if self._host_edit else DEFAULT_PROXY_HOST
        port = self._port_spin.value() if self._port_spin else DEFAULT_PROXY_PORT

        if not host:
            host = DEFAULT_PROXY_HOST

        self._status_label.setText("Testing...")
        self._status_label.setStyleSheet("color: gray;")

        # Force UI update
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        # Test basic TCP connection to proxy
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                self._status_label.setText("Connected!")
                self._status_label.setStyleSheet("color: green;")
            else:
                self._status_label.setText(f"Connection refused (error {result})")
                self._status_label.setStyleSheet("color: red;")

        except socket.timeout:
            self._status_label.setText("Connection timeout")
            self._status_label.setStyleSheet("color: red;")
        except socket.gaierror as e:
            self._status_label.setText(f"DNS error: {e}")
            self._status_label.setStyleSheet("color: red;")
        except Exception as e:
            self._status_label.setText(f"Error: {e}")
            self._status_label.setStyleSheet("color: red;")
            logger.error("Proxy test connection error: {}", e)

    def load_settings(self) -> None:
        """Load current settings into the widget.

        Populates the controls with current values from PreferencesManager.
        """
        prefs = PreferencesManager.get_instance()

        # Load enabled state
        if self._enabled_checkbox:
            self._enabled_checkbox.setChecked(prefs.get("proxy_enabled", False))

        # Load proxy type
        if self._type_combo:
            proxy_type = prefs.get("proxy_type", "socks5")
            index = self._type_combo.findData(proxy_type)
            if index >= 0:
                self._type_combo.setCurrentIndex(index)

        # Load host
        if self._host_edit:
            self._host_edit.setText(prefs.get("proxy_host", DEFAULT_PROXY_HOST))

        # Load port
        if self._port_spin:
            self._port_spin.setValue(prefs.get("proxy_port", DEFAULT_PROXY_PORT))

        # Load auto-detect
        if self._auto_detect_checkbox:
            self._auto_detect_checkbox.setChecked(prefs.get("proxy_auto_detect", False))

        # Update status label
        if self._status_label:
            self._status_label.setText("")

        self._update_enabled_state()

    def save_settings(self) -> None:
        """Save widget values to persistent storage.

        Writes the current control values to PreferencesManager.
        """
        prefs = PreferencesManager.get_instance()

        # Save enabled state
        if self._enabled_checkbox:
            prefs.set("proxy_enabled", self._enabled_checkbox.isChecked())

        # Save proxy type
        if self._type_combo:
            prefs.set("proxy_type", self._type_combo.currentData())

        # Save host
        if self._host_edit:
            host = self._host_edit.text().strip()
            prefs.set("proxy_host", host if host else DEFAULT_PROXY_HOST)

        # Save port
        if self._port_spin:
            prefs.set("proxy_port", self._port_spin.value())

        # Save auto-detect
        if self._auto_detect_checkbox:
            prefs.set("proxy_auto_detect", self._auto_detect_checkbox.isChecked())

        logger.info("Proxy settings saved")

        # Note: WebEngine proxy can only be set at startup. If user changed
        # proxy settings, they'll need to restart for WebEngine changes.

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            List of error messages, or empty list if valid.
        """
        errors = []

        # Validate host if proxy is enabled
        enabled = self._enabled_checkbox.isChecked() if self._enabled_checkbox else False
        auto_detect = self._auto_detect_checkbox.isChecked() if self._auto_detect_checkbox else False

        if enabled or auto_detect:
            if self._host_edit:
                host = self._host_edit.text().strip()
                if not host:
                    # Empty is OK - defaults to localhost
                    pass

        return errors
