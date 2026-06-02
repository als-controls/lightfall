"""Device edit/create dialog using pyqtgraph ParameterTree."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lucid.ui.dialogs.base import LucidDialog

try:
    from pyqtgraph.parametertree import Parameter, ParameterTree
    HAS_PARAMETERTREE = True
except ImportError:
    HAS_PARAMETERTREE = False

# Keys that go into fixed fields, not "extra fields"
_FIXED_KEYS = {
    "name", "display_name", "device_class", "prefix", "beamline",
    "group", "icon_override", "active",
    "_happi_result", "_state", "_ophyd_device",
    "location_group", "functional_group", "args", "kwargs", "type",
}


class DeviceEditDialog(LucidDialog):
    """Dialog for editing or creating a device."""

    def __init__(self, mode: str = "create", device: Any = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = mode
        self._device = device
        self._setup_ui()

    def _setup_ui(self) -> None:
        if self._mode == "edit" and self._device:
            self.setWindowTitle(f"Edit Device: {self._device.name}")
        else:
            self.setWindowTitle("Add New Device")
        self.setMinimumWidth(450)
        self.setMinimumHeight(500)

        layout = QVBoxLayout(self)

        self._params = self._build_params()
        self._tree = ParameterTree(showHeader=False)
        self._tree.setParameters(self._params, showTop=False)
        layout.addWidget(self._tree)

        button_row = QHBoxLayout()
        add_field_btn = QPushButton("Add Field...")
        add_field_btn.clicked.connect(self._on_add_field)
        button_row.addWidget(add_field_btn)
        button_row.addStretch()

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)
        button_row.addWidget(self._button_box)
        layout.addLayout(button_row)

    def _build_params(self) -> Parameter:
        is_edit = self._mode == "edit"
        d = self._device

        children = [
            {"name": "Identity", "type": "group", "children": [
                {"name": "name", "type": "str",
                 "value": d.name if d else "",
                 "readonly": is_edit},
                {"name": "display_name", "type": "str",
                 "value": d.display_name if d else ""},
                {"name": "device_class", "type": "str",
                 "value": d.device_class if d else "",
                 "readonly": is_edit},
            ]},
            {"name": "Connection", "type": "group", "children": [
                {"name": "prefix", "type": "str",
                 "value": d.prefix if d else ""},
                {"name": "beamline", "type": "str",
                 "value": (d.beamline or "") if d else ""},
            ]},
            {"name": "Organization", "type": "group", "children": [
                {"name": "group", "type": "str",
                 "value": d.group if d else ""},
                {"name": "icon_override", "type": "str",
                 "value": d.icon_override if d else ""},
                {"name": "active", "type": "bool",
                 "value": d.active if d else True},
            ]},
            {"name": "Extra Fields", "type": "group",
             "children": self._build_extra_fields()},
        ]
        return Parameter.create(name="Device", type="group", children=children)

    def _build_extra_fields(self) -> list[dict]:
        if not self._device or not self._device.metadata:
            return []
        children = []
        for key, value in sorted(self._device.metadata.items()):
            if key in _FIXED_KEYS or key.startswith("_"):
                continue
            children.append(self._make_extra_field_param(key, value))
        return children

    def _make_extra_field_param(self, key: str, value: Any) -> dict:
        if isinstance(value, bool):
            ptype = "bool"
        elif isinstance(value, int):
            ptype = "int"
        elif isinstance(value, float):
            ptype = "float"
        else:
            ptype = "str"
            value = str(value) if value is not None else ""
        return {"name": key, "type": ptype, "value": value, "removable": True}

    def _on_add_field(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        key, ok = QInputDialog.getText(self, "Add Field", "Field name:")
        if not ok or not key.strip():
            return
        key = key.strip()
        extra_group = self._params.child("Extra Fields")
        if extra_group.hasChildren():
            for child in extra_group.children():
                if child.name() == key:
                    QMessageBox.warning(
                        self, "Duplicate Field",
                        f"Field '{key}' already exists.",
                    )
                    return
        extra_group.addChild(self._make_extra_field_param(key, ""))

    def _on_accept(self) -> None:
        values = self.get_values()
        if not values["name"].strip():
            QMessageBox.warning(
                self, "Validation Error",
                "Device name is required.",
            )
            return
        if self._mode == "create" and not values["device_class"].strip():
            QMessageBox.warning(
                self, "Validation Error",
                "Device class is required for new devices.",
            )
            return
        self.accept()

    def get_values(self) -> dict[str, Any]:
        """Return current parameter values as a flat dict.

        Returns a dict with all fixed fields plus an ``extra_fields``
        sub-dict containing any user-added key/value pairs.
        """
        result = {
            "name": self._params.child("Identity", "name").value(),
            "display_name": self._params.child("Identity", "display_name").value(),
            "device_class": self._params.child("Identity", "device_class").value(),
            "prefix": self._params.child("Connection", "prefix").value(),
            "beamline": self._params.child("Connection", "beamline").value(),
            "group": self._params.child("Organization", "group").value(),
            "icon_override": self._params.child("Organization", "icon_override").value(),
            "active": self._params.child("Organization", "active").value(),
        }
        extra = {}
        extra_group = self._params.child("Extra Fields")
        if extra_group.hasChildren():
            for child in extra_group.children():
                extra[child.name()] = child.value()
        result["extra_fields"] = extra
        return result
