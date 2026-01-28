"""Claude Assistant settings plugin for NCS.

This module contains the ClaudeSettingsPlugin that allows users to
configure the Claude AI assistant integration.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
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
    def is_configured() -> bool:
        """Check if Claude is configured with an API key.

        Returns:
            True if an API key is available.
        """
        return ClaudeSettingsProvider.get_api_key() is not None


class ClaudeSettingsPlugin(SettingsPlugin):
    """Settings plugin for Claude Assistant configuration.

    Allows users to configure:
    - API endpoint (Anthropic, LBNL cborg, or custom)
    - API key for authentication
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
        self._env_var_label: QLabel | None = None
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
        self._update_env_var_status()
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

        # API Key field
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText("sk-ant-...")
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.textChanged.connect(self._update_env_var_status)
        layout.addRow("API Key:", self._api_key_edit)

        # Environment variable status label
        self._env_var_label = QLabel()
        self._env_var_label.setStyleSheet("color: gray; font-style: italic;")
        layout.addRow("", self._env_var_label)

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

        return group

    def _on_endpoint_changed(self, index: int) -> None:
        """Handle endpoint selection change."""
        self._update_custom_url_state()

    def _update_custom_url_state(self) -> None:
        """Update custom URL field enabled state based on endpoint selection."""
        if self._endpoint_combo and self._custom_url_edit:
            endpoint_key = self._endpoint_combo.currentData()
            is_custom = endpoint_key == "custom"
            self._custom_url_edit.setEnabled(is_custom)
            if not is_custom:
                self._custom_url_edit.clear()

    def _update_env_var_status(self) -> None:
        """Update the environment variable status label."""
        if not self._env_var_label or not self._api_key_edit:
            return

        key_text = self._api_key_edit.text().strip()
        if key_text:
            self._env_var_label.setText("")
        else:
            # Check for environment variables
            env_key = os.getenv("ANTHROPIC_API_KEY")
            env_token = os.getenv("ANTHROPIC_AUTH_TOKEN")
            if env_key:
                self._env_var_label.setText("Using env var ANTHROPIC_API_KEY")
            elif env_token:
                self._env_var_label.setText("Using env var ANTHROPIC_AUTH_TOKEN")
            else:
                self._env_var_label.setText("No API key configured")

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
        self._update_env_var_status()

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
