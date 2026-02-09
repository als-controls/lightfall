"""Claude Assistant settings plugin for NCS.

This module contains the ClaudeSettingsPlugin that allows users to
configure the Claude AI assistant integration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

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


def check_oauth_status() -> tuple[bool, str]:
    """Check if Claude Code CLI has OAuth credentials.

    Returns:
        A tuple of (is_authenticated, status_message).
    """
    credentials_path = Path.home() / ".claude" / ".credentials.json"
    if not credentials_path.exists():
        return False, "Not logged in"

    try:
        with open(credentials_path) as f:
            creds = json.load(f)
        if "claudeAiOauth" in creds:
            return True, "Logged in via OAuth (subscription)"
        return False, "No OAuth credentials"
    except (OSError, json.JSONDecodeError):
        return False, "Could not read credentials"


# Preset API endpoints
API_ENDPOINTS = {
    "anthropic": ("Anthropic API", "https://api.anthropic.com"),
    "cborg": ("LBNL Cloud (cborg)", "https://api.cborg.lbl.gov"),
    "custom": ("Custom...", ""),
}

# Default model options
MODEL_OPTIONS = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-3-7-sonnet-20250219",
    "claude-3-5-haiku-20241022",
]

# Permission mode options
PERMISSION_MODES = {
    "default": "Requires confirmation for actions",
    "acceptEdits": "Auto-accepts edits",
    "bypassPermissions": "No confirmations (automation)",
}


class ClaudeSettingsProvider:
    """Provides Claude configuration from settings/env vars.

    This helper class provides a simple interface for ClaudePanel and
    other components to retrieve Claude configuration, with proper
    fallback to environment variables.
    """

    @staticmethod
    def get_api_key() -> str | None:
        """Get API key from preferences, falling back to env vars.

        Returns:
            The API key string, or None if not configured.
        """
        prefs = PreferencesManager.get_instance()
        key = prefs.get("claude_api_key", "")
        if key:
            return key
        return os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")

    @staticmethod
    def get_base_url() -> str | None:
        """Get base URL from preferences, falling back to env vars.

        Returns:
            The base URL string, or None if using default.
        """
        prefs = PreferencesManager.get_instance()
        endpoint = prefs.get("claude_endpoint", "anthropic")
        if endpoint == "anthropic":
            return "https://api.anthropic.com"
        elif endpoint == "cborg":
            return "https://api.cborg.lbl.gov"
        else:  # custom
            url = prefs.get("claude_custom_url", "")
            if url:
                return url
        return os.getenv("ANTHROPIC_BASE_URL") or os.getenv("ANTHROPIC_API_URL")

    @staticmethod
    def get_model() -> str:
        """Get the configured model name.

        Returns:
            The model name string.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("claude_model", "claude-sonnet-4-20250514")

    @staticmethod
    def get_max_turns() -> int:
        """Get the maximum number of conversation turns.

        Returns:
            The max turns value.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("claude_max_turns", 20)

    @staticmethod
    def get_permission_mode() -> str:
        """Get the permission mode for tool execution.

        Returns:
            The permission mode string.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("claude_permission_mode", "default")

    @staticmethod
    def is_oauth_authenticated() -> bool:
        """Check if Claude Code CLI has OAuth authentication.

        Returns:
            True if OAuth credentials are available.
        """
        authenticated, _ = check_oauth_status()
        return authenticated

    @staticmethod
    def get_auth_status() -> tuple[bool, str]:
        """Get detailed authentication status.

        Returns:
            Tuple of (is_authenticated, status_message).
        """
        # Check API key first
        api_key = ClaudeSettingsProvider.get_api_key()
        if api_key:
            return True, "Using API key"

        # Check OAuth
        return check_oauth_status()

    @staticmethod
    def is_configured() -> bool:
        """Check if Claude is configured with authentication.

        Returns:
            True if API key or OAuth credentials are available.
        """
        api_key = ClaudeSettingsProvider.get_api_key()
        if api_key:
            return True
        # Fall back to OAuth check
        return ClaudeSettingsProvider.is_oauth_authenticated()

    @staticmethod
    def is_using_proxy() -> bool:
        """Check if a proxy endpoint is configured.

        Returns:
            True if using cborg or custom endpoint (not direct Anthropic API).
        """
        prefs = PreferencesManager.get_instance()
        endpoint = prefs.get("claude_endpoint", "anthropic")
        return endpoint != "anthropic"

    @staticmethod
    def get_disable_betas() -> bool:
        """Check if beta headers should be disabled.

        Returns:
            True if beta headers should be disabled (for proxy compatibility).
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("claude_disable_betas", False)


class ClaudeSettingsPlugin(SettingsPlugin):
    """Settings plugin for Claude Assistant configuration.

    Allows users to configure:
    - API endpoint (Anthropic, LBNL cborg, or custom)
    - API key for authentication (or use OAuth via `claude login`)
    - Model selection
    - Max conversation turns
    - Permission mode for tool execution
    - Test connection functionality
    """

    def __init__(self) -> None:
        """Initialize the Claude settings plugin."""
        self._widget: QWidget | None = None
        # API Configuration
        self._endpoint_combo: QComboBox | None = None
        self._custom_url_edit: QLineEdit | None = None
        self._api_key_edit: QLineEdit | None = None
        self._auth_status_label: QLabel | None = None
        self._oauth_status_label: QLabel | None = None
        self._oauth_login_button: QPushButton | None = None
        self._disable_betas_checkbox: QCheckBox | None = None
        self._test_button: QPushButton | None = None
        self._status_label: QLabel | None = None
        # Model Configuration
        self._model_combo: QComboBox | None = None
        self._max_turns_spin: QSpinBox | None = None
        # Behavior Configuration
        self._permission_combo: QComboBox | None = None

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "claude"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Claude Assistant"

    @property
    def icon(self) -> QIcon | None:
        """Return optional icon for sidebar."""
        return None

    @property
    def category(self) -> str:
        """Return category for grouping."""
        return "plugins"

    @property
    def priority(self) -> int:
        """Return sort order (lower = higher in list)."""
        return 60

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget.

        Args:
            parent: Parent widget (the dialog).

        Returns:
            A QWidget containing the Claude settings controls.
        """
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # API Configuration group
        api_group = self._create_api_group()
        layout.addWidget(api_group)

        # Model Configuration group
        model_group = self._create_model_group()
        layout.addWidget(model_group)

        # Behavior Configuration group
        behavior_group = self._create_behavior_group()
        layout.addWidget(behavior_group)

        layout.addStretch()

        self._widget = widget
        self._update_custom_url_state()
        self._update_betas_recommendation()
        self._update_oauth_status()
        self._update_auth_status()
        return widget

    def _create_api_group(self) -> QGroupBox:
        """Create the API Configuration group box.

        Returns:
            The configured QGroupBox.
        """
        group = QGroupBox("API Configuration")
        layout = QFormLayout(group)

        # API Endpoint selector
        self._endpoint_combo = QComboBox()
        for key, (label, _) in API_ENDPOINTS.items():
            self._endpoint_combo.addItem(label, key)
        self._endpoint_combo.currentIndexChanged.connect(self._on_endpoint_changed)
        layout.addRow("API Endpoint:", self._endpoint_combo)

        # Custom URL field
        self._custom_url_edit = QLineEdit()
        self._custom_url_edit.setPlaceholderText("https://your-api-server.com")
        layout.addRow("Custom URL:", self._custom_url_edit)

        # Disable betas checkbox (for proxy compatibility)
        self._disable_betas_checkbox = QCheckBox("Disable beta features (for proxy compatibility)")
        self._disable_betas_checkbox.setToolTip(
            "Disable beta headers that may not be supported by proxy servers.\n"
            "Enable this if you see errors about unsupported beta headers."
        )
        layout.addRow("", self._disable_betas_checkbox)

        # Authentication section header
        auth_header = QLabel("<b>Authentication</b>")
        layout.addRow(auth_header)

        # OAuth status section
        oauth_layout = QHBoxLayout()
        self._oauth_status_label = QLabel()
        oauth_layout.addWidget(self._oauth_status_label)

        self._oauth_login_button = QPushButton("Login with Browser")
        self._oauth_login_button.setToolTip("Run 'claude login' to authenticate with your Claude subscription")
        self._oauth_login_button.clicked.connect(self._on_oauth_login)
        oauth_layout.addWidget(self._oauth_login_button)
        oauth_layout.addStretch()

        layout.addRow("OAuth Status:", oauth_layout)

        # API Key field (alternative to OAuth)
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText("sk-ant-... (optional if using OAuth)")
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.textChanged.connect(self._update_auth_status)
        layout.addRow("API Key:", self._api_key_edit)

        # Authentication status label
        self._auth_status_label = QLabel()
        self._auth_status_label.setStyleSheet("font-style: italic;")
        layout.addRow("", self._auth_status_label)

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

    def _create_model_group(self) -> QGroupBox:
        """Create the Model Configuration group box.

        Returns:
            The configured QGroupBox.
        """
        group = QGroupBox("Model Configuration")
        layout = QFormLayout(group)

        # Model selector (editable combo for custom models)
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        for model in MODEL_OPTIONS:
            self._model_combo.addItem(model)
        layout.addRow("Model:", self._model_combo)

        # Max turns spinner
        self._max_turns_spin = QSpinBox()
        self._max_turns_spin.setRange(1, 100)
        self._max_turns_spin.setValue(20)
        self._max_turns_spin.setToolTip(
            "Maximum number of conversation turns before stopping"
        )
        layout.addRow("Max Turns:", self._max_turns_spin)

        return group

    def _create_behavior_group(self) -> QGroupBox:
        """Create the Behavior Configuration group box.

        Returns:
            The configured QGroupBox.
        """
        group = QGroupBox("Behavior Configuration")
        layout = QFormLayout(group)

        # Permission mode selector
        self._permission_combo = QComboBox()
        for key, description in PERMISSION_MODES.items():
            self._permission_combo.addItem(f"{key} - {description}", key)
        layout.addRow("Permission Mode:", self._permission_combo)

        # Description
        desc = QLabel(
            "Permission mode controls how Claude handles tool execution:\n"
            "- default: Asks for confirmation before actions\n"
            "- acceptEdits: Auto-accepts file edits\n"
            "- bypassPermissions: No confirmations (use for automation)"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        layout.addRow(desc)

        # Proxy compatibility warning
        proxy_note = QLabel(
            "<b>Note:</b> Some proxy servers (LBNL cborg, custom) may not support all "
            "Claude Code CLI features. If you see errors about unsupported beta headers, "
            "try using the direct Anthropic API endpoint instead."
        )
        proxy_note.setWordWrap(True)
        proxy_note.setStyleSheet("color: #666; font-size: 9pt;")
        layout.addRow(proxy_note)

        return group

    def _on_endpoint_changed(self, index: int) -> None:
        """Handle endpoint selection change."""
        self._update_custom_url_state()
        self._update_betas_recommendation()

    def _update_custom_url_state(self) -> None:
        """Update custom URL field enabled state based on endpoint selection."""
        if self._endpoint_combo and self._custom_url_edit:
            endpoint_key = self._endpoint_combo.currentData()
            is_custom = endpoint_key == "custom"
            self._custom_url_edit.setEnabled(is_custom)
            if not is_custom:
                self._custom_url_edit.clear()

    def _update_betas_recommendation(self) -> None:
        """Update betas checkbox recommendation based on endpoint."""
        if not self._endpoint_combo or not self._disable_betas_checkbox:
            return

        endpoint_key = self._endpoint_combo.currentData()
        is_proxy = endpoint_key != "anthropic"

        if is_proxy:
            self._disable_betas_checkbox.setToolTip(
                "RECOMMENDED for proxy endpoints.\n"
                "Disable beta headers that may not be supported by proxy servers.\n"
                "Enable this if you see errors about unsupported beta headers."
            )
        else:
            self._disable_betas_checkbox.setToolTip(
                "Disable beta features. Usually not needed for direct Anthropic API."
            )

    def _on_oauth_login(self) -> None:
        """Handle OAuth login button click."""
        import subprocess
        import sys

        if self._oauth_status_label:
            self._oauth_status_label.setText("Opening browser...")
            self._oauth_status_label.setStyleSheet("color: gray; font-style: italic;")

        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        try:
            # Run claude login
            if sys.platform == "win32":
                subprocess.Popen(["claude", "login"], shell=True)
            else:
                subprocess.Popen(["claude", "login"])

            if self._oauth_status_label:
                self._oauth_status_label.setText("Browser opened - complete login there")
                self._oauth_status_label.setStyleSheet("color: #0066cc; font-style: italic;")
        except FileNotFoundError:
            if self._oauth_status_label:
                self._oauth_status_label.setText("Claude CLI not found. Install with: npm i -g @anthropic-ai/claude-code")
                self._oauth_status_label.setStyleSheet("color: red; font-style: italic;")
        except Exception as e:
            logger.error("OAuth login error: {}", e)
            if self._oauth_status_label:
                self._oauth_status_label.setText(f"Error: {e}")
                self._oauth_status_label.setStyleSheet("color: red; font-style: italic;")

    def _update_oauth_status(self) -> None:
        """Update the OAuth status display."""
        if not self._oauth_status_label:
            return

        authenticated, message = check_oauth_status()
        if authenticated:
            self._oauth_status_label.setText(message)
            self._oauth_status_label.setStyleSheet("color: green; font-style: italic;")
        else:
            self._oauth_status_label.setText(message)
            self._oauth_status_label.setStyleSheet("color: gray; font-style: italic;")

    def _update_auth_status(self) -> None:
        """Update the authentication status label."""
        if not self._auth_status_label or not self._api_key_edit:
            return

        key_text = self._api_key_edit.text().strip()
        if key_text:
            self._auth_status_label.setText("Using API key from settings")
            self._auth_status_label.setStyleSheet("color: green; font-style: italic;")
        else:
            # Check for environment variables
            env_key = os.getenv("ANTHROPIC_API_KEY")
            env_token = os.getenv("ANTHROPIC_AUTH_TOKEN")
            if env_key:
                self._auth_status_label.setText("Using env var ANTHROPIC_API_KEY")
                self._auth_status_label.setStyleSheet("color: green; font-style: italic;")
            elif env_token:
                self._auth_status_label.setText("Using env var ANTHROPIC_AUTH_TOKEN")
                self._auth_status_label.setStyleSheet("color: green; font-style: italic;")
            else:
                # Check OAuth
                authenticated, _ = check_oauth_status()
                if authenticated:
                    self._auth_status_label.setText("Using OAuth (no API key needed)")
                    self._auth_status_label.setStyleSheet("color: green; font-style: italic;")
                else:
                    self._auth_status_label.setText("No authentication configured")
                    self._auth_status_label.setStyleSheet("color: orange; font-style: italic;")

    def _on_test_connection(self) -> None:
        """Handle test connection button click."""
        if not self._status_label:
            return

        # Get current settings from UI
        api_key = self._get_effective_api_key()
        base_url = self._get_effective_base_url()

        if not api_key:
            self._status_label.setText("No API key configured")
            self._status_label.setStyleSheet("color: orange;")
            return

        self._status_label.setText("Testing...")
        self._status_label.setStyleSheet("color: gray;")

        # Force UI update
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.processEvents()

        try:
            import httpx

            # Make a minimal API request to test the connection
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }

            # Use the messages endpoint with a minimal request
            url = f"{base_url}/v1/messages"
            data = {
                "model": "claude-3-5-haiku-20241022",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Hi"}],
            }

            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=data, headers=headers)

                if response.status_code == 200:
                    self._status_label.setText("Connected!")
                    self._status_label.setStyleSheet("color: green;")
                elif response.status_code == 401:
                    self._status_label.setText("Invalid API key")
                    self._status_label.setStyleSheet("color: red;")
                elif response.status_code == 403:
                    self._status_label.setText("Access denied")
                    self._status_label.setStyleSheet("color: red;")
                else:
                    self._status_label.setText(f"Error: HTTP {response.status_code}")
                    self._status_label.setStyleSheet("color: red;")

        except httpx.TimeoutException:
            self._status_label.setText("Connection timeout")
            self._status_label.setStyleSheet("color: red;")
        except httpx.ConnectError:
            self._status_label.setText("Could not connect to server")
            self._status_label.setStyleSheet("color: red;")
        except Exception as e:
            self._status_label.setText(f"Error: {e}")
            self._status_label.setStyleSheet("color: red;")
            logger.error("Test connection error: {}", e)

    def _get_effective_api_key(self) -> str | None:
        """Get the effective API key from UI or environment.

        Returns:
            The API key or None.
        """
        if self._api_key_edit:
            key = self._api_key_edit.text().strip()
            if key:
                return key
        return os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")

    def _get_effective_base_url(self) -> str:
        """Get the effective base URL from UI.

        Returns:
            The base URL string.
        """
        if self._endpoint_combo:
            endpoint_key = self._endpoint_combo.currentData()
            if endpoint_key == "anthropic":
                return "https://api.anthropic.com"
            elif endpoint_key == "cborg":
                return "https://api.cborg.lbl.gov"
            elif endpoint_key == "custom" and self._custom_url_edit:
                url = self._custom_url_edit.text().strip()
                if url:
                    return url.rstrip("/")
        return "https://api.anthropic.com"

    def load_settings(self) -> None:
        """Load current settings into the widget.

        Populates the controls with current values from PreferencesManager.
        """
        prefs = PreferencesManager.get_instance()

        # Load endpoint selection
        if self._endpoint_combo:
            endpoint = prefs.get("claude_endpoint", "anthropic")
            index = self._endpoint_combo.findData(endpoint)
            if index >= 0:
                self._endpoint_combo.setCurrentIndex(index)

        # Load custom URL
        if self._custom_url_edit:
            self._custom_url_edit.setText(prefs.get("claude_custom_url", ""))

        # Load disable betas setting
        if self._disable_betas_checkbox:
            self._disable_betas_checkbox.setChecked(prefs.get("claude_disable_betas", False))

        # Load API key
        if self._api_key_edit:
            self._api_key_edit.setText(prefs.get("claude_api_key", ""))

        # Load model
        if self._model_combo:
            model = prefs.get("claude_model", "claude-sonnet-4-20250514")
            index = self._model_combo.findText(model)
            if index >= 0:
                self._model_combo.setCurrentIndex(index)
            else:
                self._model_combo.setEditText(model)

        # Load max turns
        if self._max_turns_spin:
            self._max_turns_spin.setValue(prefs.get("claude_max_turns", 20))

        # Load permission mode
        if self._permission_combo:
            mode = prefs.get("claude_permission_mode", "default")
            index = self._permission_combo.findData(mode)
            if index >= 0:
                self._permission_combo.setCurrentIndex(index)

        # Update status labels
        if self._status_label:
            self._status_label.setText("")

        self._update_custom_url_state()
        self._update_betas_recommendation()
        self._update_oauth_status()
        self._update_auth_status()

    def save_settings(self) -> None:
        """Save widget values to persistent storage.

        Writes the current control values to PreferencesManager.
        """
        prefs = PreferencesManager.get_instance()

        # Save endpoint
        if self._endpoint_combo:
            endpoint = self._endpoint_combo.currentData()
            prefs.set("claude_endpoint", endpoint)

        # Save custom URL
        if self._custom_url_edit:
            prefs.set("claude_custom_url", self._custom_url_edit.text().strip())

        # Save disable betas setting
        if self._disable_betas_checkbox:
            prefs.set("claude_disable_betas", self._disable_betas_checkbox.isChecked())

        # Save API key
        if self._api_key_edit:
            prefs.set("claude_api_key", self._api_key_edit.text().strip())

        # Save model
        if self._model_combo:
            prefs.set("claude_model", self._model_combo.currentText())

        # Save max turns
        if self._max_turns_spin:
            prefs.set("claude_max_turns", self._max_turns_spin.value())

        # Save permission mode
        if self._permission_combo:
            mode = self._permission_combo.currentData()
            prefs.set("claude_permission_mode", mode)

        logger.info("Claude settings saved")

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            List of error messages, or empty list if valid.
        """
        errors = []

        # Check custom URL if custom endpoint is selected
        if self._endpoint_combo and self._custom_url_edit:
            endpoint_key = self._endpoint_combo.currentData()
            if endpoint_key == "custom":
                url = self._custom_url_edit.text().strip()
                if not url:
                    errors.append("Custom URL is required when using custom endpoint")
                elif not url.startswith(("http://", "https://")):
                    errors.append("Custom URL must start with http:// or https://")

        return errors
