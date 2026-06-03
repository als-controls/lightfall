"""Device selector for plan parameters.

Provides a custom pyqtgraph ParameterTree type for selecting devices from
the DeviceCatalog. Includes a dialog for device selection and a custom
parameter type that integrates with the ParameterTree system.

Usage:
    In parameter specs, use type='device':
        {'name': 'detectors', 'type': 'device', 'multi_select': True, 'catalog': catalog}
        {'name': 'motor', 'type': 'device', 'multi_select': False, 'catalog': catalog}

    With device filters:
        {'name': 'motor', 'type': 'device', 'categories': {DeviceCategory.MOTOR}}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.models.device_selection import DeviceSelectionFilterProxy, DeviceSelectionModel

try:
    from pyqtgraph.parametertree import Parameter
    from pyqtgraph.parametertree.Parameter import PARAM_TYPES
    from pyqtgraph.parametertree.parameterTypes import StrParameterItem, registerParameterType

    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False
    Parameter = None
    StrParameterItem = object
    registerParameterType = None
    PARAM_TYPES = {}

if TYPE_CHECKING:
    from collections.abc import Callable

    from lightfall.devices import DeviceCatalog


# ---------------------------------------------------------------------------
# Part 1 – Icon resolution
# ---------------------------------------------------------------------------

_CATEGORY_ICON_MAP = {
    "motor": "mdi6.engine",
    "detector": "mdi6.camera",
    "controller": "mdi6.tune-variant",
}
_DEFAULT_ICON = "mdi6.microwave"


def resolve_button_icon_name(icon: str | None, categories: set | None) -> str:
    """Resolution: explicit icon > auto from single category > default.

    Args:
        icon: Explicit icon name, or None to auto-detect.
        categories: Set of DeviceCategory values, or None.

    Returns:
        QtAwesome icon identifier string (always prefixed with "mdi6.").
    """
    if icon is not None:
        return icon if "." in icon else f"mdi6.{icon}"
    if categories is not None and len(categories) == 1:
        cat = next(iter(categories))
        cat_value = cat.value if hasattr(cat, "value") else str(cat)
        return _CATEGORY_ICON_MAP.get(cat_value, _DEFAULT_ICON)
    return _DEFAULT_ICON


# ---------------------------------------------------------------------------
# Part 2 – DeviceSelectorDialog
# ---------------------------------------------------------------------------


class DeviceSelectorDialog(QDialog):
    """Dialog for selecting devices from the catalog using a QTreeView.

    Replaces the old QListWidget-based dialog with a model/proxy architecture.

    Args:
        catalog: DeviceCatalog to select from.
        multi_select: Allow multiple device selection.
        show_tree: Show ophyd component tree beneath each device.
        categories: Restrict to these DeviceCategory values.
        writable_only: Only show writable signals.
        kinds: Only show items with these ophyd kind strings.
        filter_func: Custom filter callable(metadata_dict) -> bool.
        sort_key: Custom sort callable(metadata_dict) -> comparable.
        initial_selection: Dotted paths to pre-check on open.
        parent: Parent widget.
    """

    def __init__(
        self,
        catalog: DeviceCatalog,
        *,
        multi_select: bool = True,
        show_tree: bool = False,
        categories: set | None = None,
        writable_only: bool = False,
        kinds: set[str] | None = None,
        filter_func: Callable | None = None,
        sort_key: Callable | None = None,
        initial_selection: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._multi_select = multi_select

        # Store original filters for "Show all" toggle
        self._orig_categories = categories
        self._orig_writable_only = writable_only
        self._orig_kinds = kinds
        self._orig_filter_func = filter_func
        self._has_filters = (
            categories is not None or writable_only or kinds is not None or filter_func is not None
        )

        # Build model + proxy
        self._model = DeviceSelectionModel(catalog, show_tree=show_tree)
        self._proxy = DeviceSelectionFilterProxy()
        self._proxy.setSourceModel(self._model)

        # Apply filters
        if categories is not None:
            self._proxy.set_categories(categories)
        if writable_only:
            self._proxy.set_writable_only(True)
        if kinds is not None:
            self._proxy.set_kinds(kinds)
        if filter_func is not None:
            self._proxy.set_filter_func(filter_func)
        if sort_key is not None:
            self._proxy.set_sort_key(sort_key)

        self._setup_ui(show_tree=show_tree)

        # Sort alphabetically by default
        self._proxy.sort(0)

        # Connect model data changes to info label updater
        self._model.dataChanged.connect(self._on_data_changed)

        # Apply initial selection
        if initial_selection:
            self.set_selected_paths(initial_selection)

    def _setup_ui(self, show_tree: bool = False) -> None:
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
        self._search_edit.textChanged.connect(self._proxy.set_search_text)
        search_layout.addWidget(self._search_edit)
        layout.addLayout(search_layout)

        # Show all devices checkbox (only when filters are active)
        if self._has_filters:
            self._show_all_check = QCheckBox("Show all devices")
            self._show_all_check.setChecked(False)
            self._show_all_check.stateChanged.connect(self._on_show_all_toggled)
            layout.addWidget(self._show_all_check)
        else:
            self._show_all_check = None

        # Tree view
        self._tree_view = QTreeView()
        self._tree_view.setModel(self._proxy)
        self._tree_view.setHeaderHidden(True)
        self._tree_view.setRootIsDecorated(show_tree)
        layout.addWidget(self._tree_view)

        # Info label
        self._info_label = QLabel("0 devices selected")
        layout.addWidget(self._info_label)

        # OK / Cancel
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    @Slot(int)
    def _on_show_all_toggled(self, state: int) -> None:
        """Toggle between filtered and unfiltered device list."""
        show_all = state == Qt.CheckState.Checked.value
        if show_all:
            self._proxy.set_categories(None)
            self._proxy.set_writable_only(False)
            self._proxy.set_kinds(None)
            self._proxy.set_filter_func(None)
        else:
            if self._orig_categories is not None:
                self._proxy.set_categories(self._orig_categories)
            if self._orig_writable_only:
                self._proxy.set_writable_only(True)
            if self._orig_kinds is not None:
                self._proxy.set_kinds(self._orig_kinds)
            if self._orig_filter_func is not None:
                self._proxy.set_filter_func(self._orig_filter_func)

    @Slot()
    def _on_data_changed(self) -> None:
        """Update info label; enforce single-select when multi_select=False."""
        paths = self._model.get_checked_paths()
        count = len(paths)

        if not self._multi_select and count > 1:
            # Keep only the last checked path
            self._model.set_checked_paths([paths[-1]])
            paths = [paths[-1]]
            count = 1

        if count == 0:
            self._info_label.setText("0 devices selected")
        elif count == 1:
            self._info_label.setText(f"1 device selected: {paths[0]}")
        else:
            self._info_label.setText(f"{count} devices selected")

    def get_selected_paths(self) -> list[str]:
        """Return dotted paths of all checked items.

        Returns:
            List of checked dotted-path strings.
        """
        return self._model.get_checked_paths()

    def set_selected_paths(self, paths: list[str]) -> None:
        """Check items by dotted path; uncheck everything else.

        Args:
            paths: Dotted-path strings to check.
        """
        self._model.set_checked_paths(paths)


# ---------------------------------------------------------------------------
# Part 3 – pyqtgraph DeviceParameterItem + DeviceParameter
# ---------------------------------------------------------------------------

if HAS_PYQTGRAPH:

    class DeviceParameterItem(StrParameterItem):
        """Parameter item for device selection with a dialog button.

        Similar to FileParameterItem, shows a read-only text field with
        a button that opens a device selection dialog.
        """

        def __init__(self, param, depth):
            self._value: list[str] = []
            super().__init__(param, depth)

            # Add icon button to open dialog
            self._select_button = QPushButton()
            self._select_button.setFixedWidth(25)
            self._select_button.setContentsMargins(0, 0, 0, 0)
            self._select_button.clicked.connect(self._open_device_dialog)
            self._apply_button_icon()
            self.layoutWidget.layout().insertWidget(2, self._select_button)

            # Handle resize for text elision
            self.displayLabel.resizeEvent = self._new_resize_event

        def _apply_button_icon(self) -> None:
            """Set icon on the select button using QtAwesome."""
            opts = self.param.opts
            icon_name = resolve_button_icon_name(
                opts.get("icon"), opts.get("categories")
            )
            try:
                import qtawesome as qta
                self._select_button.setIcon(qta.icon(icon_name))
            except (ImportError, Exception):
                self._select_button.setText("...")

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

            dialog = DeviceSelectorDialog(
                catalog,
                multi_select=opts.get("multi_select", True),
                show_tree=opts.get("show_tree", False),
                categories=opts.get("categories"),
                writable_only=opts.get("writable_only", False),
                kinds=opts.get("kinds"),
                filter_func=opts.get("filter_func"),
                sort_key=opts.get("sort_key"),
                parent=None,
            )

            # Pre-select current values
            current = self.param.value() if self.param.hasValue() else []
            if current:
                dialog.set_selected_paths(current)

            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected = dialog.get_selected_paths()
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

            if isinstance(value, list):
                value = ", ".join(value) if value else ""
            else:
                value = str(value) if value else ""

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
            show_tree: Show ophyd component tree (default: False).
            categories: Set of DeviceCategory to filter by.
            writable_only: Only show writable signals (default: False).
            kinds: Set of kind strings to filter by.
            filter_func: Custom filter callable(metadata_dict) -> bool.
            sort_key: Custom sort callable(metadata_dict) -> comparable.
            icon: QtAwesome icon name for the button (auto-resolved if omitted).

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
