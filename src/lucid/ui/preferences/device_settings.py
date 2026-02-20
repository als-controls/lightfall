"""Device backend settings plugin for NCS.

Allows enabling multiple device backends simultaneously, each with
its own configuration section.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lucid.plugins.settings_plugin import SettingsPlugin
from lucid.ui.preferences.manager import PreferencesManager

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


class DeviceSettingsPlugin(SettingsPlugin):
    """Settings plugin for device backend configuration.

    Users can enable multiple backends simultaneously. Each backend has
    its own enable checkbox and configuration group. Devices from all
    enabled backends are merged into a single catalog.

    Note: Changes require application restart to take effect.
    """

    def __init__(self) -> None:
        self._widget: QWidget | None = None

        # Mock
        self._mock_enabled: QCheckBox | None = None
        self._mock_noisy_check: QComboBox | None = None

        # BCS
        self._bcs_enabled: QCheckBox | None = None
        self._bcs_host_edit: QLineEdit | None = None
        self._bcs_port_spin: QSpinBox | None = None
        self._bcs_beamline_edit: QLineEdit | None = None
        self._bcs_timeout_spin: QSpinBox | None = None

        # Happi
        self._happi_enabled: QCheckBox | None = None
        self._happi_path_edit: QLineEdit | None = None
        self._happi_beamline_edit: QLineEdit | None = None
        self._happi_instantiate: QCheckBox | None = None

    @property
    def name(self) -> str:
        return "devices"

    @property
    def display_name(self) -> str:
        return "Devices"

    @property
    def icon(self) -> QIcon | None:
        return None

    @property
    def category(self) -> str:
        return "general"

    @property
    def priority(self) -> int:
        return 20

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        # Extra top padding so checkable QGroupBox indicators aren't clipped
        widget.setStyleSheet(
            "QGroupBox { margin-top: 10px; padding-top: 16px; }"
            "QGroupBox::title { subcontrol-origin: margin; padding: 0 4px; }"
        )

        # Restart notice
        notice = QLabel(
            "<i>⚠ Changes to device backends require application restart.</i>"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)

        # Mock backend
        layout.addWidget(self._create_mock_group())

        # BCS backend
        layout.addWidget(self._create_bcs_group())

        # Happi backend
        layout.addWidget(self._create_happi_group())

        layout.addStretch()
        self._widget = widget
        return widget

    # ── Mock ──────────────────────────────────────────────────────

    def _create_mock_group(self) -> QGroupBox:
        group = QGroupBox("Mock Backend")
        group.setCheckable(True)
        self._mock_enabled = group  # QGroupBox.isChecked()
        form = QFormLayout(group)

        self._mock_noisy_check = QComboBox()
        self._mock_noisy_check.addItem("Yes", True)
        self._mock_noisy_check.addItem("No", False)
        form.addRow("Include noisy devices:", self._mock_noisy_check)

        desc = QLabel(
            "Simulated ophyd.sim devices for development and testing."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        form.addRow(desc)

        return group

    # ── BCS ───────────────────────────────────────────────────────

    def _create_bcs_group(self) -> QGroupBox:
        group = QGroupBox("BCS Backend (ZMQ)")
        group.setCheckable(True)
        self._bcs_enabled = group

        form = QFormLayout(group)

        self._bcs_host_edit = QLineEdit()
        self._bcs_host_edit.setPlaceholderText("localhost")
        form.addRow("Server host:", self._bcs_host_edit)

        port_layout = QHBoxLayout()
        self._bcs_port_spin = QSpinBox()
        self._bcs_port_spin.setRange(1, 65535)
        self._bcs_port_spin.setValue(5577)
        port_layout.addWidget(self._bcs_port_spin)
        port_layout.addStretch()
        form.addRow("Server port:", port_layout)

        self._bcs_beamline_edit = QLineEdit()
        self._bcs_beamline_edit.setPlaceholderText("(optional)")
        form.addRow("Beamline:", self._bcs_beamline_edit)

        timeout_layout = QHBoxLayout()
        self._bcs_timeout_spin = QSpinBox()
        self._bcs_timeout_spin.setRange(100, 60000)
        self._bcs_timeout_spin.setValue(5000)
        self._bcs_timeout_spin.setSuffix(" ms")
        self._bcs_timeout_spin.setSingleStep(500)
        timeout_layout.addWidget(self._bcs_timeout_spin)
        timeout_layout.addStretch()
        form.addRow("Timeout:", timeout_layout)

        desc = QLabel(
            "Connects to a BCS server via ZMQ and auto-discovers devices "
            "using bcsophyd. Requires bcsophyd package."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        form.addRow(desc)

        return group

    # ── Happi ─────────────────────────────────────────────────────

    def _create_happi_group(self) -> QGroupBox:
        group = QGroupBox("Happi Backend")
        group.setCheckable(True)
        self._happi_enabled = group

        form = QFormLayout(group)

        self._happi_path_edit = QLineEdit()
        self._happi_path_edit.setPlaceholderText("Path to happi JSON db (or leave empty for $HAPPI_BACKEND)")
        form.addRow("Database path:", self._happi_path_edit)

        self._happi_beamline_edit = QLineEdit()
        self._happi_beamline_edit.setPlaceholderText("(optional — filter by beamline)")
        form.addRow("Beamline filter:", self._happi_beamline_edit)

        self._happi_instantiate = QCheckBox("Instantiate ophyd devices on load")
        self._happi_instantiate.setToolTip(
            "If checked, happi will call item.get() to create live ophyd device "
            "instances. Leave unchecked to load metadata only (faster, no EPICS needed)."
        )
        form.addRow(self._happi_instantiate)

        desc = QLabel(
            "Loads devices from a Happi device database. "
            "Supports JSON file backends and happi's default configuration. "
            "Requires happi package."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        form.addRow(desc)

        return group

    # ── Load / Save ───────────────────────────────────────────────

    def load_settings(self) -> None:
        prefs = PreferencesManager.get_instance()

        # Which backends are enabled (legacy compat: "device_backend" = "mock" or "bcs")
        legacy_backend = prefs.get("device_backend", "mock")
        mock_on = prefs.get("device_mock_enabled", legacy_backend == "mock")
        bcs_on = prefs.get("device_bcs_enabled", legacy_backend == "bcs")
        happi_on = prefs.get("device_happi_enabled", False)

        # Mock
        if isinstance(self._mock_enabled, QGroupBox):
            self._mock_enabled.setChecked(mock_on)
        if self._mock_noisy_check:
            idx = self._mock_noisy_check.findData(prefs.get("device_mock_include_noisy", True))
            if idx >= 0:
                self._mock_noisy_check.setCurrentIndex(idx)

        # BCS
        if isinstance(self._bcs_enabled, QGroupBox):
            self._bcs_enabled.setChecked(bcs_on)
        if self._bcs_host_edit:
            self._bcs_host_edit.setText(prefs.get("device_bcs_host", "localhost"))
        if self._bcs_port_spin:
            self._bcs_port_spin.setValue(prefs.get("device_bcs_port", 5577))
        if self._bcs_beamline_edit:
            self._bcs_beamline_edit.setText(prefs.get("device_bcs_beamline", ""))
        if self._bcs_timeout_spin:
            self._bcs_timeout_spin.setValue(prefs.get("device_bcs_timeout_ms", 5000))

        # Happi
        if isinstance(self._happi_enabled, QGroupBox):
            self._happi_enabled.setChecked(happi_on)
        if self._happi_path_edit:
            self._happi_path_edit.setText(prefs.get("device_happi_path", ""))
        if self._happi_beamline_edit:
            self._happi_beamline_edit.setText(prefs.get("device_happi_beamline", ""))
        if self._happi_instantiate:
            self._happi_instantiate.setChecked(prefs.get("device_happi_instantiate", False))

    def save_settings(self) -> None:
        prefs = PreferencesManager.get_instance()

        mock_on = self._mock_enabled.isChecked() if isinstance(self._mock_enabled, QGroupBox) else False
        bcs_on = self._bcs_enabled.isChecked() if isinstance(self._bcs_enabled, QGroupBox) else False
        happi_on = self._happi_enabled.isChecked() if isinstance(self._happi_enabled, QGroupBox) else False

        prefs.set("device_mock_enabled", mock_on)
        prefs.set("device_bcs_enabled", bcs_on)
        prefs.set("device_happi_enabled", happi_on)

        # Legacy compat: set device_backend to first enabled
        if bcs_on:
            prefs.set("device_backend", "bcs")
        elif happi_on:
            prefs.set("device_backend", "happi")
        else:
            prefs.set("device_backend", "mock")

        # Mock
        if self._mock_noisy_check:
            prefs.set("device_mock_include_noisy", self._mock_noisy_check.currentData())

        # BCS
        if self._bcs_host_edit:
            prefs.set("device_bcs_host", self._bcs_host_edit.text() or "localhost")
        if self._bcs_port_spin:
            prefs.set("device_bcs_port", self._bcs_port_spin.value())
        if self._bcs_beamline_edit:
            prefs.set("device_bcs_beamline", self._bcs_beamline_edit.text())
        if self._bcs_timeout_spin:
            prefs.set("device_bcs_timeout_ms", self._bcs_timeout_spin.value())

        # Happi
        if self._happi_path_edit:
            prefs.set("device_happi_path", self._happi_path_edit.text())
        if self._happi_beamline_edit:
            prefs.set("device_happi_beamline", self._happi_beamline_edit.text())
        if self._happi_instantiate:
            prefs.set("device_happi_instantiate", self._happi_instantiate.isChecked())

    def validate(self) -> list[str]:
        errors = []

        bcs_on = self._bcs_enabled.isChecked() if isinstance(self._bcs_enabled, QGroupBox) else False
        if bcs_on:
            if self._bcs_host_edit and not self._bcs_host_edit.text().strip():
                errors.append("BCS server host is required when BCS backend is enabled")

        return errors
