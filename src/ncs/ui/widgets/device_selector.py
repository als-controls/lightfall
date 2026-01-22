"""Device selector widget for plan parameters.

Provides a reusable widget for selecting devices from the DeviceCatalog,
used in plan configuration for parameters like 'detectors' or 'motor'.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ncs.devices import DeviceCatalog, DeviceInfo


# Device category icons (color, letter) - matches device_tree.py
DEVICE_CATEGORY_ICONS: dict[str, tuple[str, str]] = {
    "motor": ("#4CAF50", "M"),  # Green
    "detector": ("#2196F3", "D"),  # Blue
    "camera": ("#9C27B0", "C"),  # Purple
    "sensor": ("#FF9800", "S"),  # Orange
    "signal": ("#607D8B", "s"),  # Gray
    "positioner": ("#4CAF50", "P"),  # Green
    "controller": ("#795548", "K"),  # Brown
    "other": ("#9E9E9E", "?"),  # Gray
}


def create_device_icon(color: str, letter: str, size: int = 16) -> QIcon:
    """Create a simple colored icon with a letter.

    Args:
        color: Hex color string for the background.
        letter: Single letter to display.
        size: Icon size in pixels.

    Returns:
        QIcon with colored circle and letter.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Draw colored circle
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)

    # Draw letter
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(10)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, letter)

    painter.end()

    return QIcon(pixmap)


class DeviceSelectorDialog(QDialog):
    """Dialog for selecting devices from the catalog.

    Provides a searchable list of devices with multi-selection support.

    Args:
        catalog: DeviceCatalog to select from.
        multi_select: Allow multiple device selection.
        category_filter: Filter devices by category (e.g., "detector", "motor").
        parent: Parent widget.
    """

    def __init__(
        self,
        catalog: DeviceCatalog,
        multi_select: bool = True,
        category_filter: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._multi_select = multi_select
        self._category_filter = category_filter
        self._selected_names: list[str] = []
        self._icons: dict[str, QIcon] = {}

        self._setup_ui()
        self._populate_devices()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        self.setWindowTitle("Select Devices")
        self.setMinimumSize(400, 500)

        layout = QVBoxLayout(self)

        # Search box
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter devices...")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_search_changed)
        search_layout.addWidget(self._search_edit)
        layout.addLayout(search_layout)

        # Device list
        self._device_list = QListWidget()
        if self._multi_select:
            self._device_list.setSelectionMode(
                QAbstractItemView.SelectionMode.MultiSelection
            )
        else:
            self._device_list.setSelectionMode(
                QAbstractItemView.SelectionMode.SingleSelection
            )
        self._device_list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._device_list)

        # Selection info
        self._info_label = QLabel("0 devices selected")
        layout.addWidget(self._info_label)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _get_icon(self, category: str) -> QIcon:
        """Get or create icon for a device category."""
        if category not in self._icons:
            color, letter = DEVICE_CATEGORY_ICONS.get(
                category, DEVICE_CATEGORY_ICONS["other"]
            )
            self._icons[category] = create_device_icon(color, letter)
        return self._icons[category]

    def _populate_devices(self) -> None:
        """Populate the device list from the catalog."""
        self._device_list.clear()

        devices = self._catalog.get_all_devices()

        for device in sorted(devices, key=lambda d: d.name):
            # Apply category filter if set
            if self._category_filter:
                if device.category.value != self._category_filter:
                    continue

            item = QListWidgetItem(device.name)
            item.setData(Qt.ItemDataRole.UserRole, device)
            item.setToolTip(f"{device.description or device.name}\n"
                           f"Category: {device.category.value}\n"
                           f"Prefix: {device.prefix}")
            item.setIcon(self._get_icon(device.category.value))
            self._device_list.addItem(item)

    @Slot(str)
    def _on_search_changed(self, text: str) -> None:
        """Filter the device list based on search text."""
        text_lower = text.lower()

        for i in range(self._device_list.count()):
            item = self._device_list.item(i)
            device: DeviceInfo = item.data(Qt.ItemDataRole.UserRole)

            # Match against name, description, prefix
            visible = (
                text_lower in device.name.lower()
                or text_lower in device.description.lower()
                or text_lower in device.prefix.lower()
            )
            item.setHidden(not visible)

    @Slot()
    def _on_selection_changed(self) -> None:
        """Update selection info when selection changes."""
        selected = self._device_list.selectedItems()
        count = len(selected)

        if count == 0:
            self._info_label.setText("0 devices selected")
        elif count == 1:
            self._info_label.setText(f"1 device selected: {selected[0].text()}")
        else:
            self._info_label.setText(f"{count} devices selected")

    def set_selected_names(self, names: list[str]) -> None:
        """Pre-select devices by name.

        Args:
            names: List of device names to select.
        """
        for i in range(self._device_list.count()):
            item = self._device_list.item(i)
            device: DeviceInfo = item.data(Qt.ItemDataRole.UserRole)
            item.setSelected(device.name in names)

    def get_selected_names(self) -> list[str]:
        """Get the names of selected devices.

        Returns:
            List of selected device names.
        """
        names = []
        for item in self._device_list.selectedItems():
            device: DeviceInfo = item.data(Qt.ItemDataRole.UserRole)
            names.append(device.name)
        return names

    def get_selected_devices(self) -> list[DeviceInfo]:
        """Get the selected DeviceInfo objects.

        Returns:
            List of selected DeviceInfo objects.
        """
        devices = []
        for item in self._device_list.selectedItems():
            device: DeviceInfo = item.data(Qt.ItemDataRole.UserRole)
            devices.append(device)
        return devices


class DeviceSelectorWidget(QWidget):
    """Compact widget for selecting devices, embeddable in forms.

    Shows a text field with selected device names and a button to open
    the selection dialog.

    Signals:
        selection_changed: Emitted when the selection changes.
            Argument is list of device names.

    Args:
        catalog: DeviceCatalog to select from.
        multi_select: Allow multiple device selection.
        category_filter: Filter devices by category.
        parent: Parent widget.
    """

    selection_changed = Signal(list)  # list[str] device names

    def __init__(
        self,
        catalog: DeviceCatalog | None = None,
        multi_select: bool = True,
        category_filter: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._multi_select = multi_select
        self._category_filter = category_filter
        self._selected_names: list[str] = []

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the widget UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Display current selection
        self._display = QLineEdit()
        self._display.setReadOnly(True)
        self._display.setPlaceholderText("No devices selected")
        layout.addWidget(self._display)

        # Select button
        self._select_btn = QPushButton("...")
        self._select_btn.setFixedWidth(30)
        self._select_btn.setToolTip("Select devices")
        self._select_btn.clicked.connect(self._on_select_clicked)
        layout.addWidget(self._select_btn)

        # Clear button
        self._clear_btn = QPushButton("x")
        self._clear_btn.setFixedWidth(24)
        self._clear_btn.setToolTip("Clear selection")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        layout.addWidget(self._clear_btn)

    def set_catalog(self, catalog: DeviceCatalog) -> None:
        """Set the device catalog.

        Args:
            catalog: DeviceCatalog to use.
        """
        self._catalog = catalog

    def set_category_filter(self, category: str | None) -> None:
        """Set the category filter.

        Args:
            category: Category to filter by, or None for all.
        """
        self._category_filter = category

    def set_multi_select(self, multi: bool) -> None:
        """Set whether multiple selection is allowed.

        Args:
            multi: True for multi-select, False for single.
        """
        self._multi_select = multi

    def get_selected_names(self) -> list[str]:
        """Get the currently selected device names.

        Returns:
            List of device names.
        """
        return list(self._selected_names)

    def set_selected_names(self, names: list[str]) -> None:
        """Set the selected device names.

        Args:
            names: List of device names to select.
        """
        self._selected_names = list(names)
        self._update_display()

    def _update_display(self) -> None:
        """Update the display text."""
        if not self._selected_names:
            self._display.setText("")
        else:
            self._display.setText(", ".join(self._selected_names))

    @Slot()
    def _on_select_clicked(self) -> None:
        """Open the device selection dialog."""
        if self._catalog is None:
            logger.warning("No DeviceCatalog set for DeviceSelectorWidget")
            return

        dialog = DeviceSelectorDialog(
            catalog=self._catalog,
            multi_select=self._multi_select,
            category_filter=self._category_filter,
            parent=self,
        )
        dialog.set_selected_names(self._selected_names)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._selected_names = dialog.get_selected_names()
            self._update_display()
            self.selection_changed.emit(self._selected_names)
            logger.debug(f"Device selection changed: {self._selected_names}")

    @Slot()
    def _on_clear_clicked(self) -> None:
        """Clear the selection."""
        if self._selected_names:
            self._selected_names = []
            self._update_display()
            self.selection_changed.emit(self._selected_names)

    def get_value(self) -> list[str]:
        """Get the widget value (for parameter tree integration).

        Returns:
            List of selected device names.
        """
        return self.get_selected_names()

    def set_value(self, value: list[str] | str) -> None:
        """Set the widget value (for parameter tree integration).

        Args:
            value: Device name(s) as list or comma-separated string.
        """
        if isinstance(value, str):
            if value:
                names = [n.strip() for n in value.split(",")]
            else:
                names = []
        else:
            names = list(value)
        self.set_selected_names(names)
