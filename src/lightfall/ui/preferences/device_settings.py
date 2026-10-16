"""Device backend settings plugin for NCS.

Holds a list of backend rows. Each row is a backend type (mock, BCS,
happi, CSM, or a plugin-contributed type) with its own name and
configuration, and several rows may share a type.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.devices.backend_types.base import BackendEditor
from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import (
    BackendRow,
    load_rows,
    migrate_legacy,
    save_rows,
    unique_name,
)
from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.manager import PreferencesManager

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon

_COL_ENABLED, _COL_NAME, _COL_TYPE, _COL_SUMMARY = range(4)


class DeviceSettingsPlugin(SettingsPlugin):
    """Settings plugin for device backend configuration.

    The page lists backend rows in a table; selecting a row shows that
    type's settings form below it. Devices from all enabled rows are
    merged into a single catalog.

    Note: Changes require application restart to take effect.
    """

    def __init__(self) -> None:
        self._widget: QWidget | None = None

        # Connection settings
        self._connection_timeout_spin: QDoubleSpinBox | None = None
        self._connect_on_startup: QCheckBox | None = None
        self._instantiate_mode: QComboBox | None = None

        # CA Tunnel
        self._ca_tunnel_enabled: QCheckBox | None = None
        self._ca_tunnel_gateway: QLineEdit | None = None

        # Backend rows
        self._rows: list[BackendRow] = []
        self._editors: dict[str, QWidget] = {}  # row id -> editor (or placeholder)
        self._table: QTableWidget | None = None
        self._stack: QStackedWidget | None = None
        self._name_edit: QLineEdit | None = None
        self._detail_group: QGroupBox | None = None

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
        # The registry reuses this plugin across Preferences dialogs; editors
        # from a previous dialog were destroyed with its widget tree, so start
        # a fresh map for the new stack built below.
        self._editors = {}
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        # Prevent checkbox indicator clipping on Windows dark themes
        widget.setStyleSheet("""
            QGroupBox {
                margin-top: 7px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                top: 2px;
                left: 8px;
            }
            QGroupBox::indicator {
                subcontrol-origin: margin;
                top: 2px;
            }
        """)

        # Restart notice
        notice = QLabel("<i>⚠ Changes to device backends require application restart.</i>")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        # Connection settings (global)
        layout.addWidget(self._create_connection_group())

        # Backend rows and the selected row's settings
        layout.addWidget(self._create_backends_group())
        layout.addWidget(self._create_detail_group())

        layout.addStretch()
        self._widget = widget
        return widget

    # ── Connection Settings ─────────────────────────────────────────

    def _create_connection_group(self) -> QGroupBox:
        group = QGroupBox("Connection Settings")
        form = QFormLayout(group)
        form.setContentsMargins(12, 20, 12, 12)

        # Instantiation mode
        self._instantiate_mode = QComboBox()
        self._instantiate_mode.addItem("Background (recommended)", "background")
        self._instantiate_mode.addItem("On demand (manual)", "none")
        self._instantiate_mode.addItem("Blocking (legacy)", "blocking")
        self._instantiate_mode.setToolTip(
            "Background: Devices connect in background threads (fastest startup)\n"
            "On demand: Only connect when a device is selected\n"
            "Blocking: Connect all devices synchronously on startup (can be slow)"
        )
        form.addRow("Device instantiation:", self._instantiate_mode)

        # Connection timeout
        timeout_layout = QHBoxLayout()
        self._connection_timeout_spin = QDoubleSpinBox()
        self._connection_timeout_spin.setRange(0.5, 120.0)
        self._connection_timeout_spin.setValue(5.0)
        self._connection_timeout_spin.setSuffix(" seconds")
        self._connection_timeout_spin.setSingleStep(0.5)
        self._connection_timeout_spin.setDecimals(1)
        self._connection_timeout_spin.setToolTip(
            "How long to wait for a device to connect before marking it as failed.\n"
            "Increase this if you have slow devices or network latency."
        )
        timeout_layout.addWidget(self._connection_timeout_spin)
        timeout_layout.addStretch()
        form.addRow("Connection timeout:", timeout_layout)

        # Connect on startup
        self._connect_on_startup = QCheckBox("Connect devices on startup")
        self._connect_on_startup.setToolTip(
            "If checked, devices will start connecting when the application starts.\n"
            "If unchecked, devices only connect when you select them."
        )
        form.addRow(self._connect_on_startup)

        desc = QLabel(
            "Controls how device connections are handled. Background mode connects "
            "devices in separate threads so the UI stays responsive."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        form.addRow(desc)

        # CA Tunnel (remote access)
        tunnel_label = QLabel("<b>Remote Access (CA Tunnel)</b>")
        form.addRow(tunnel_label)

        self._ca_tunnel_enabled = QCheckBox("Enable CA tunnel for remote access")
        self._ca_tunnel_enabled.setToolTip(
            "Bridges local UDP CA traffic to a remote CA Gateway via TCP.\n"
            "Requires an SSH tunnel forwarding the gateway port, and a\n"
            "CA Gateway running on the remote host.\n\n"
            "SSH example: ssh -L 5099:localhost:5099 user@host"
        )
        self._ca_tunnel_enabled.toggled.connect(self._on_tunnel_toggled)
        form.addRow(self._ca_tunnel_enabled)

        self._ca_tunnel_gateway = QLineEdit()
        self._ca_tunnel_gateway.setPlaceholderText("localhost:5099")
        self._ca_tunnel_gateway.setToolTip(
            "Address of the CA Gateway as host:port.\n"
            "This should match your SSH tunnel's local forwarded port."
        )
        self._ca_tunnel_gateway.setEnabled(False)
        form.addRow("Gateway address:", self._ca_tunnel_gateway)

        tunnel_desc = QLabel(
            "For remote development: forwards EPICS Channel Access through "
            "an SSH tunnel to a CA Gateway on the beamline network. "
            "Requires restart."
        )
        tunnel_desc.setWordWrap(True)
        tunnel_desc.setStyleSheet("color: gray;")
        form.addRow(tunnel_desc)

        return group

    def _on_tunnel_toggled(self, checked: bool) -> None:
        """Enable/disable the gateway address field."""
        if self._ca_tunnel_gateway:
            self._ca_tunnel_gateway.setEnabled(checked)

    # ── Backends table ──────────────────────────────────────────────

    def _create_backends_group(self) -> QGroupBox:
        group = QGroupBox("Backends")
        box = QVBoxLayout(group)
        box.setContentsMargins(12, 20, 12, 12)

        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["", "Name", "Type", "Details"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(_COL_ENABLED, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_SUMMARY, QHeaderView.ResizeMode.Stretch)
        table.itemSelectionChanged.connect(self._on_selection_changed)
        table.itemChanged.connect(self._on_item_changed)
        self._table = table
        box.addWidget(table)

        buttons = QHBoxLayout()
        add = QToolButton()
        add.setText("Add")
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(add)
        for backend_type in BackendTypeRegistry.get_instance().all():
            action = menu.addAction(backend_type.display_name)
            action.triggered.connect(lambda _=False, t=backend_type.type_id: self._add_row(t))
        add.setMenu(menu)
        duplicate = QPushButton("Duplicate")
        duplicate.clicked.connect(self._duplicate_selected)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_selected)
        buttons.addWidget(add)
        buttons.addWidget(duplicate)
        buttons.addWidget(remove)
        buttons.addStretch()
        box.addLayout(buttons)
        return group

    def _create_detail_group(self) -> QGroupBox:
        group = QGroupBox("Backend settings")
        box = QVBoxLayout(group)
        box.setContentsMargins(12, 20, 12, 12)
        form = QFormLayout()
        self._name_edit = QLineEdit()
        self._name_edit.textChanged.connect(self._on_name_edited)
        form.addRow("Name:", self._name_edit)
        box.addLayout(form)
        self._stack = QStackedWidget()
        box.addWidget(self._stack)
        self._detail_group = group
        group.setEnabled(False)
        return group

    # ── Row helpers ─────────────────────────────────────────────────

    def _selected_index(self) -> int:
        if self._table is None:
            return -1
        rows = self._table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _select(self, index: int) -> None:
        if self._table is not None and 0 <= index < len(self._rows):
            self._table.selectRow(index)

    def _type_label(self, row: BackendRow) -> str:
        backend_type = BackendTypeRegistry.get_instance().get(row.type)
        return backend_type.display_name if backend_type else f"(unavailable: {row.type})"

    def _summary(self, row: BackendRow) -> str:
        backend_type = BackendTypeRegistry.get_instance().get(row.type)
        return backend_type.summary(row.config) if backend_type else ""

    def _refresh_table(self) -> None:
        if self._table is None:
            return
        selected = self._selected_index()
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._rows))
        for index, row in enumerate(self._rows):
            enabled = QTableWidgetItem()
            enabled.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            enabled.setCheckState(Qt.CheckState.Checked if row.enabled else Qt.CheckState.Unchecked)
            self._table.setItem(index, _COL_ENABLED, enabled)
            self._table.setItem(index, _COL_NAME, QTableWidgetItem(row.name))
            self._table.setItem(index, _COL_TYPE, QTableWidgetItem(self._type_label(row)))
            self._table.setItem(index, _COL_SUMMARY, QTableWidgetItem(self._summary(row)))
        self._table.blockSignals(False)
        if 0 <= selected < len(self._rows):
            self._table.selectRow(selected)

    def _editor_for(self, row: BackendRow) -> QWidget:
        editor = self._editors.get(row.id)
        if editor is not None:
            return editor
        backend_type = BackendTypeRegistry.get_instance().get(row.type)
        if backend_type is None:
            editor = QLabel(
                f"Backend type '{row.type}' is not available (is its plugin installed?). "
                "This row is kept unchanged."
            )
            editor.setWordWrap(True)
        else:
            editor = backend_type.create_editor(row.config, self._stack)
            editor.changed.connect(lambda rid=row.id: self._on_editor_changed(rid))
        self._editors[row.id] = editor
        assert self._stack is not None
        self._stack.addWidget(editor)
        return editor

    def _sync_row(self, row: BackendRow) -> None:
        editor = self._editors.get(row.id)
        if isinstance(editor, BackendEditor):
            row.config = editor.get_config()

    def _row_by_id(self, row_id: str) -> tuple[int, BackendRow] | None:
        for index, row in enumerate(self._rows):
            if row.id == row_id:
                return index, row
        return None

    # ── Slots ───────────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        index = self._selected_index()
        if self._detail_group is None or self._name_edit is None or self._stack is None:
            return
        # Loading a row into the name field is not an edit of that row.
        self._name_edit.blockSignals(True)
        try:
            if index < 0:
                self._detail_group.setEnabled(False)
                self._name_edit.clear()
                return
            row = self._rows[index]
            self._detail_group.setEnabled(True)
            self._detail_group.setTitle(f"{row.name} ({self._type_label(row)})")
            self._name_edit.setText(row.name)
        finally:
            self._name_edit.blockSignals(False)
        self._stack.setCurrentWidget(self._editor_for(row))

    def _on_editor_changed(self, row_id: str) -> None:
        found = self._row_by_id(row_id)
        if found is None or self._table is None:
            return
        index, row = found
        self._sync_row(row)
        item = self._table.item(index, _COL_SUMMARY)
        if item is not None:
            item.setText(self._summary(row))

    def _on_name_edited(self, text: str) -> None:
        index = self._selected_index()
        if index < 0 or self._table is None:
            return
        row = self._rows[index]
        row.name = text.strip()
        item = self._table.item(index, _COL_NAME)
        if item is not None:
            item.setText(row.name)
        if self._detail_group is not None:
            self._detail_group.setTitle(f"{row.name} ({self._type_label(row)})")

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == _COL_ENABLED and 0 <= item.row() < len(self._rows):
            self._rows[item.row()].enabled = item.checkState() == Qt.CheckState.Checked

    def _add_row(self, type_id: str) -> None:
        backend_type = BackendTypeRegistry.get_instance().get(type_id)
        if backend_type is None:
            return
        row = BackendRow(
            type=type_id,
            name=unique_name(type_id, (r.name for r in self._rows)),
            config=backend_type.default_config(),
        )
        self._rows.append(row)
        self._refresh_table()
        self._select(len(self._rows) - 1)

    def _duplicate_selected(self) -> None:
        index = self._selected_index()
        if index < 0:
            return
        source = self._rows[index]
        self._sync_row(source)
        row = BackendRow(
            type=source.type,
            name=unique_name(source.type, (r.name for r in self._rows)),
            enabled=source.enabled,
            config=deepcopy(source.config),
        )
        self._rows.insert(index + 1, row)
        self._refresh_table()
        self._select(index + 1)

    def _remove_selected(self) -> None:
        index = self._selected_index()
        if index < 0:
            return
        row = self._rows.pop(index)
        editor = self._editors.pop(row.id, None)
        if editor is not None and self._stack is not None:
            self._stack.removeWidget(editor)
            editor.deleteLater()
        self._refresh_table()
        self._select(min(index, len(self._rows) - 1))
        if not self._rows:
            self._on_selection_changed()

    # ── Load / Save ─────────────────────────────────────────────────

    def load_settings(self) -> None:
        prefs = PreferencesManager.get_instance()

        # Connection settings
        if self._instantiate_mode:
            mode = prefs.get("device_instantiate_mode", "background")
            idx = self._instantiate_mode.findData(mode)
            if idx >= 0:
                self._instantiate_mode.setCurrentIndex(idx)

        if self._connection_timeout_spin:
            self._connection_timeout_spin.setValue(prefs.get("device_connection_timeout", 5.0))

        if self._connect_on_startup:
            self._connect_on_startup.setChecked(prefs.get("device_connect_on_startup", True))

        # CA Tunnel
        if self._ca_tunnel_enabled:
            self._ca_tunnel_enabled.setChecked(prefs.get("ca_tunnel_enabled", False))
        if self._ca_tunnel_gateway:
            self._ca_tunnel_gateway.setText(prefs.get("ca_tunnel_gateway", "localhost:5099"))
            self._ca_tunnel_gateway.setEnabled(prefs.get("ca_tunnel_enabled", False))

        # Backend rows
        rows = load_rows(prefs.get)
        if rows is None:
            rows = migrate_legacy(prefs.get)  # shown, not saved, until the user saves
        for editor in self._editors.values():
            if self._stack is not None:
                self._stack.removeWidget(editor)
            editor.deleteLater()
        self._editors.clear()
        self._rows = rows
        self._refresh_table()
        self._select(0)

    def save_settings(self) -> None:
        prefs = PreferencesManager.get_instance()

        # Connection settings
        if self._instantiate_mode:
            prefs.set("device_instantiate_mode", self._instantiate_mode.currentData())

        if self._connection_timeout_spin:
            prefs.set("device_connection_timeout", self._connection_timeout_spin.value())

        if self._connect_on_startup:
            prefs.set("device_connect_on_startup", self._connect_on_startup.isChecked())

        # CA Tunnel
        if self._ca_tunnel_enabled:
            prefs.set("ca_tunnel_enabled", self._ca_tunnel_enabled.isChecked())
        if self._ca_tunnel_gateway:
            prefs.set("ca_tunnel_gateway", self._ca_tunnel_gateway.text() or "localhost:5099")

        # Backend rows
        for row in self._rows:
            self._sync_row(row)
        save_rows(prefs, self._rows)

    def validate(self) -> list[str]:
        errors: list[str] = []
        seen: set[str] = set()
        registry = BackendTypeRegistry.get_instance()
        for row in self._rows:
            self._sync_row(row)
            if not row.name:
                errors.append("Every backend needs a name")
                continue
            key = row.name.lower()
            if key in seen:
                errors.append(f"Backend names must be unique: '{row.name}' is used twice")
            seen.add(key)
            backend_type = registry.get(row.type)
            if row.enabled and backend_type is not None:
                errors.extend(f"{row.name}: {e}" for e in backend_type.validate(row.config))
        return errors
