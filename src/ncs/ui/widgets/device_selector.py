"""Device selector for plan parameters.

Provides a custom pyqtgraph ParameterTree type for selecting devices from
the DeviceCatalog. Includes a dialog for device selection and a custom
parameter type that integrates with the ParameterTree system.

Usage:
    In parameter specs, use type='device':
        {'name': 'detectors', 'type': 'device', 'multi_select': True, 'catalog': catalog}
        {'name': 'motor', 'type': 'device', 'multi_select': False, 'catalog': catalog}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QFontMetricsF, QIcon, QPainter, QPixmap
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

try:
    from pyqtgraph.parametertree import Parameter
    from pyqtgraph.parametertree.parameterTypes import StrParameterItem, registerParameterType
    from pyqtgraph.parametertree.Parameter import PARAM_TYPES

    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False
    Parameter = None
    StrParameterItem = object
    registerParameterType = None
    PARAM_TYPES = {}

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


if HAS_PYQTGRAPH:

    class DeviceParameterItem(StrParameterItem):
        """Parameter item for device selection with dialog button.

        Similar to FileParameterItem, shows a read-only text field with
        a button that opens a device selection dialog.
        """

        def __init__(self, param, depth):
            self._value: list[str] = []
            super().__init__(param, depth)

            # Add the "..." button to open dialog
            self._select_button = QPushButton("...")
            self._select_button.setFixedWidth(25)
            self._select_button.setContentsMargins(0, 0, 0, 0)
            self._select_button.clicked.connect(self._open_device_dialog)
            self.layoutWidget.layout().insertWidget(2, self._select_button)

            # Handle resize for text elision
            self.displayLabel.resizeEvent = self._new_resize_event

        def showEditor(self):
            """Show the editor widget, keeping button visible."""
            super().showEditor()
            self._select_button.show()

        def hideEditor(self):
            """Hide the editor widget, keeping button visible."""
            super().hideEditor()
            self._select_button.show()

        def makeWidget(self):
            """Create the widget for editing."""
            w = super().makeWidget()
            w.setValue = self.setValue
            w.value = self.value
            # Remove sigChanging since selection is complete when dialog closes
            if hasattr(w, "sigChanging"):
                delattr(w, "sigChanging")
            return w

        def _new_resize_event(self, ev):
            """Handle resize to update elided text."""
            ret = type(self.displayLabel).resizeEvent(self.displayLabel, ev)
            self.updateDisplayLabel()
            return ret

        def setValue(self, value):
            """Set the parameter value."""
            if isinstance(value, str):
                if value:
                    self._value = [n.strip() for n in value.split(",")]
                else:
                    self._value = []
            elif isinstance(value, list):
                self._value = list(value)
            else:
                self._value = []

            # Update widget display
            display_text = ", ".join(self._value) if self._value else ""
            self.widget.setText(display_text)

        def value(self):
            """Get the current value."""
            return self._value

        def _open_device_dialog(self):
            """Open the device selection dialog."""
            opts = self.param.opts
            catalog = opts.get("catalog")

            if catalog is None:
                logger.warning("No DeviceCatalog set for DeviceParameter")
                return

            multi_select = opts.get("multi_select", True)
            category_filter = opts.get("category_filter")

            dialog = DeviceSelectorDialog(
                catalog=catalog,
                multi_select=multi_select,
                category_filter=category_filter,
                parent=None,
            )

            # Pre-select current values
            current = self.param.value() if self.param.hasValue() else []
            if current:
                dialog.set_selected_names(current)

            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected = dialog.get_selected_names()
                self.param.setValue(selected)

        def updateDefaultBtn(self):
            """Update the default button state."""
            self.defaultBtn.setEnabled(
                not self.param.valueIsDefault() and self.param.opts["enabled"]
            )
            self.defaultBtn.setVisible(self.param.hasDefault())

        def updateDisplayLabel(self, value=None):
            """Update the display label with elided text."""
            lbl = self.displayLabel
            if value is None:
                value = self.param.value()

            # Format as comma-separated string
            if isinstance(value, list):
                value = ", ".join(value) if value else ""
            else:
                value = str(value) if value else ""

            # Elide text if too long
            font = lbl.font()
            metrics = QFontMetricsF(font)
            value = metrics.elidedText(
                value, Qt.TextElideMode.ElideRight, lbl.width() - 5
            )
            return super().updateDisplayLabel(value)

    class DeviceParameter(Parameter):
        """Parameter type for selecting devices from a DeviceCatalog.

        Options:
            catalog: DeviceCatalog instance to select devices from.
            multi_select: If True, allow selecting multiple devices (default: True).
            category_filter: Optional category string to filter devices by.

        Example:
            >>> params = Parameter.create(name='params', type='group', children=[
            ...     {'name': 'detectors', 'type': 'device', 'catalog': catalog, 'multi_select': True},
            ...     {'name': 'motor', 'type': 'device', 'catalog': catalog, 'multi_select': False},
            ... ])
        """

        itemClass = DeviceParameterItem

        def __init__(self, **opts):
            opts.setdefault("readonly", True)
            opts.setdefault("value", [])
            opts.setdefault("multi_select", True)
            super().__init__(**opts)

    # Register the parameter type
    if "device" not in PARAM_TYPES:
        registerParameterType("device", DeviceParameter)
        logger.debug("Registered 'device' parameter type")
