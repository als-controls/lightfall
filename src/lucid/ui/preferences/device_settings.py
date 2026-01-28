"""Device backend settings plugin for NCS.

This module contains the DeviceSettingsPlugin that allows users to
select and configure the device backend (Mock, BCS, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.ui.preferences.manager import PreferencesManager

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


class DeviceSettingsPlugin(SettingsPlugin):
    """Settings plugin for device backend configuration.

    Allows users to select and configure the device backend:
    - Mock: Simulated devices for development/testing
    - BCS: Connect to a BCS server via ZMQ

    Note: Changes to the device backend require an application restart
    to take effect.
    """

    def __init__(self) -> None:
        """Initialize the device settings plugin."""
        self._widget: QWidget | None = None
        self._backend_combo: QComboBox | None = None
        self._options_stack: QStackedWidget | None = None

        # Mock backend options
        self._mock_noisy_check: QComboBox | None = None

        # BCS backend options
        self._bcs_host_edit: QLineEdit | None = None
        self._bcs_port_spin: QSpinBox | None = None
        self._bcs_beamline_edit: QLineEdit | None = None
        self._bcs_timeout_spin: QSpinBox | None = None

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "devices"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Devices"

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
        return 20  # After appearance (0)

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the settings widget.

        Args:
            parent: Parent widget (the dialog).

        Returns:
            A QWidget containing the device settings controls.
        """
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Backend selection group
        backend_group = QGroupBox("Device Backend")
        backend_layout = QFormLayout(backend_group)

        # Backend selector
        self._backend_combo = QComboBox()
        self._backend_combo.addItem("Mock (Simulated)", "mock")
        self._backend_combo.addItem("BCS (ZMQ)", "bcs")
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        backend_layout.addRow("Backend:", self._backend_combo)

        # Restart notice
        notice = QLabel(
            "<i>Note: Changes require application restart to take effect.</i>"
        )
        notice.setWordWrap(True)
        backend_layout.addRow(notice)

        layout.addWidget(backend_group)

        # Stacked widget for backend-specific options
        self._options_stack = QStackedWidget()

        # Mock backend options page
        mock_page = self._create_mock_options()
        self._options_stack.addWidget(mock_page)

        # BCS backend options page
        bcs_page = self._create_bcs_options()
        self._options_stack.addWidget(bcs_page)

        layout.addWidget(self._options_stack)
        layout.addStretch()

        self._widget = widget
        return widget

    def _create_mock_options(self) -> QWidget:
        """Create the Mock backend options widget."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("Mock Backend Options")
        form = QFormLayout(group)

        # Include noisy devices option
        self._mock_noisy_check = QComboBox()
        self._mock_noisy_check.addItem("Yes", True)
        self._mock_noisy_check.addItem("No", False)
        form.addRow("Include noisy devices:", self._mock_noisy_check)

        # Description
        desc = QLabel(
            "Mock backend provides simulated ophyd.sim devices for "
            "development and testing. No external connections required."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        form.addRow(desc)

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _create_bcs_options(self) -> QWidget:
        """Create the BCS backend options widget."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("BCS Backend Options")
        form = QFormLayout(group)

        # Host
        self._bcs_host_edit = QLineEdit()
        self._bcs_host_edit.setPlaceholderText("localhost")
        form.addRow("Server host:", self._bcs_host_edit)

        # Port
        port_layout = QHBoxLayout()
        self._bcs_port_spin = QSpinBox()
        self._bcs_port_spin.setRange(1, 65535)
        self._bcs_port_spin.setValue(5577)
        port_layout.addWidget(self._bcs_port_spin)
        port_layout.addStretch()
        form.addRow("Server port:", port_layout)

        # Beamline
        self._bcs_beamline_edit = QLineEdit()
        self._bcs_beamline_edit.setPlaceholderText("(optional)")
        form.addRow("Beamline:", self._bcs_beamline_edit)

        # Timeout
        timeout_layout = QHBoxLayout()
        self._bcs_timeout_spin = QSpinBox()
        self._bcs_timeout_spin.setRange(100, 60000)
        self._bcs_timeout_spin.setValue(5000)
        self._bcs_timeout_spin.setSuffix(" ms")
        self._bcs_timeout_spin.setSingleStep(500)
        timeout_layout.addWidget(self._bcs_timeout_spin)
        timeout_layout.addStretch()
        form.addRow("Timeout:", timeout_layout)

        # Description
        desc = QLabel(
            "BCS backend connects to a Beamline Control System server via ZMQ "
            "and auto-discovers devices from the Happi database."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        form.addRow(desc)

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _on_backend_changed(self, index: int) -> None:
        """Handle backend selection change."""
        if self._options_stack:
            self._options_stack.setCurrentIndex(index)

    def load_settings(self) -> None:
        """Load current settings into the widget.

        Populates the controls with current values from PreferencesManager.
        """
        if not self._backend_combo:
            return

        prefs = PreferencesManager.get_instance()

        # Load backend selection
        backend = prefs.get("device_backend", "mock")
        index = self._backend_combo.findData(backend)
        if index >= 0:
            self._backend_combo.setCurrentIndex(index)
            self._on_backend_changed(index)

        # Load mock options
        if self._mock_noisy_check:
            include_noisy = prefs.get("device_mock_include_noisy", True)
            noisy_index = self._mock_noisy_check.findData(include_noisy)
            if noisy_index >= 0:
                self._mock_noisy_check.setCurrentIndex(noisy_index)

        # Load BCS options
        if self._bcs_host_edit:
            self._bcs_host_edit.setText(prefs.get("device_bcs_host", "localhost"))
        if self._bcs_port_spin:
            self._bcs_port_spin.setValue(prefs.get("device_bcs_port", 5577))
        if self._bcs_beamline_edit:
            self._bcs_beamline_edit.setText(prefs.get("device_bcs_beamline", ""))
        if self._bcs_timeout_spin:
            self._bcs_timeout_spin.setValue(prefs.get("device_bcs_timeout_ms", 5000))

    def save_settings(self) -> None:
        """Save widget values to persistent storage.

        Writes the current control values to PreferencesManager.
        """
        if not self._backend_combo:
            return

        prefs = PreferencesManager.get_instance()

        # Save backend selection
        prefs.set("device_backend", self._backend_combo.currentData())

        # Save mock options
        if self._mock_noisy_check:
            prefs.set("device_mock_include_noisy", self._mock_noisy_check.currentData())

        # Save BCS options
        if self._bcs_host_edit:
            prefs.set("device_bcs_host", self._bcs_host_edit.text() or "localhost")
        if self._bcs_port_spin:
            prefs.set("device_bcs_port", self._bcs_port_spin.value())
        if self._bcs_beamline_edit:
            prefs.set("device_bcs_beamline", self._bcs_beamline_edit.text())
        if self._bcs_timeout_spin:
            prefs.set("device_bcs_timeout_ms", self._bcs_timeout_spin.value())

    def validate(self) -> list[str]:
        """Validate current widget values.

        Returns:
            List of error messages, or empty list if valid.
        """
        errors = []

        # Validate BCS host if BCS backend selected
        if self._backend_combo and self._backend_combo.currentData() == "bcs":
            if self._bcs_host_edit and not self._bcs_host_edit.text().strip():
                errors.append("BCS server host is required")

        return errors
