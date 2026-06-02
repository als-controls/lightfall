"""
PVAutoWidget - automatically selects widget type based on PV characteristics.

Inspects the PV data type and metadata to choose the most appropriate
display/edit widget (Label, LineEdit, or ComboBox).
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Property, Signal, Slot
from PySide6.QtWidgets import QVBoxLayout, QWidget

from lightfall.epics.widgets.base import EpicsWidget
from lightfall.epics.widgets.combobox import PVComboBox
from lightfall.epics.widgets.label import PVLabel
from lightfall.epics.widgets.lineedit import PVLineEdit


class PVAutoWidget(EpicsWidget):
    """
    A widget that automatically selects display type based on PV characteristics.

    The widget inspects the PV's data type and metadata to determine the
    most appropriate representation:

    - Enum PVs -> PVComboBox (dropdown selection)
    - Numeric PVs (int, float, double) -> PVLineEdit (editable) or PVLabel (readonly)
    - String PVs -> PVLineEdit (editable) or PVLabel (readonly)
    - Array PVs -> PVLabel (display only, shows formatted array)

    The widget type is determined after connection when the PV type is known.

    Attributes:
        auto_readonly: If True, arrays and certain types are automatically readonly.
        prefer_label: If True, prefer PVLabel over PVLineEdit for simple values.

    Signals:
        widget_type_changed: Emitted when the internal widget type changes.

    Example:
        >>> auto = PVAutoWidget("ANY:PV:NAME")
        >>> # Widget type determined automatically after connection
    """

    widget_type: ClassVar[str] = "PVAutoWidget"
    widget_description: ClassVar[str] = "Auto-selecting widget based on PV type"

    widget_type_changed = Signal(str)

    # EPICS data type categories
    ENUM_TYPES = {"DBF_ENUM", "enum"}
    STRING_TYPES = {"DBF_STRING", "string", "char"}
    INTEGER_TYPES = {"DBF_SHORT", "DBF_INT", "DBF_LONG", "short", "int", "long", "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"}
    FLOAT_TYPES = {"DBF_FLOAT", "DBF_DOUBLE", "float", "double", "float32", "float64"}

    def __init__(
        self,
        pv_name: str = "",
        parent: QWidget | None = None,
        readonly: bool = False,
        auto_readonly: bool = True,
        prefer_label: bool = False,
    ) -> None:
        """
        Initialize the auto widget.

        Args:
            pv_name: The EPICS PV name to connect to.
            parent: Optional Qt parent widget.
            readonly: If True, always use read-only display.
            auto_readonly: If True, arrays are automatically readonly.
            prefer_label: If True, prefer PVLabel for scalar values.
        """
        self._auto_readonly = auto_readonly
        self._prefer_label = prefer_label
        self._current_widget_type = "unknown"
        self._inner_widget: EpicsWidget | None = None
        self._pv_data_type: str | None = None
        self._is_array = False

        super().__init__(pv_name, parent, readonly)

        # Create layout with stacked widget for swapping
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        # Placeholder until we know the PV type
        self._placeholder = QWidget()
        self._layout.addWidget(self._placeholder)

    @Property(bool)
    def auto_readonly(self) -> bool:
        """Whether arrays are automatically readonly."""
        return self._auto_readonly

    @auto_readonly.setter
    def auto_readonly(self, value: bool) -> None:
        self._auto_readonly = value

    @Property(bool)
    def prefer_label(self) -> bool:
        """Whether to prefer PVLabel over PVLineEdit."""
        return self._prefer_label

    @prefer_label.setter
    def prefer_label(self, value: bool) -> None:
        self._prefer_label = value
        if self._connected:
            self._select_widget_type()

    @Property(str)
    def current_widget_type(self) -> str:
        """The currently selected widget type name."""
        return self._current_widget_type

    def _connect_pv(self) -> None:
        """
        Override to intercept PV connection and inspect type.
        """
        if not self._pv_name:
            return

        from lightfall.epics.ca.pv import PV

        self._pv = PV(self._pv_name, parent=self)
        self._pv.value_changed.connect(self._on_pv_value_changed)
        self._pv.connection_changed.connect(self._on_pv_connection_changed)
        self._pv.metadata_changed.connect(self._on_pv_metadata_changed)
        self._pv.connect_pv()

    @Slot(object)
    def _on_pv_value_changed(self, value: Any) -> None:
        """Handle initial value to determine array status."""
        self._value = value

        # Check if array
        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            self._is_array = len(value) > 1
        else:
            self._is_array = False

        # Select widget type if not yet selected
        if self._inner_widget is None and self._connected:
            self._select_widget_type()
        elif self._inner_widget is not None:
            # Forward to inner widget
            self._inner_widget._value = value
            self._inner_widget._update_display()

        self.value_changed.emit(value)

    @Slot(bool)
    def _on_pv_connection_changed(self, connected: bool) -> None:
        """Handle connection state change."""
        self._connected = connected
        self._update_connection_style()
        self._update_readonly_state()

        if connected and self._inner_widget is None:
            # Try to determine type from PV
            self._inspect_pv_type()

        if self._inner_widget is not None:
            self._inner_widget._connected = connected
            self._inner_widget._update_connection_style()
            self._inner_widget._update_readonly_state()

        self.connection_changed.emit(connected)

    @Slot(dict)
    def _on_pv_metadata_changed(self, metadata: dict[str, Any]) -> None:
        """Handle metadata to detect enum type."""
        # Check for enum strings - definitive indicator of enum type
        if "enum_strings" in metadata and metadata["enum_strings"]:
            self._pv_data_type = "enum"

        # Select widget if we have enough info
        if self._inner_widget is None and self._connected:
            self._select_widget_type()
        elif self._inner_widget is not None:
            # Forward metadata to inner widget
            self._inner_widget._on_pv_metadata_changed(metadata)

    def _inspect_pv_type(self) -> None:
        """
        Inspect the caproto PV to determine data type.
        """
        if self._pv is None or self._pv._caproto_pv is None:
            return

        try:
            caproto_pv = self._pv._caproto_pv
            if hasattr(caproto_pv, "channel") and caproto_pv.channel is not None:
                channel = caproto_pv.channel
                if hasattr(channel, "native_data_type"):
                    dtype = channel.native_data_type
                    self._pv_data_type = self._categorize_channel_type(dtype)
        except Exception:
            pass

    def _categorize_channel_type(self, dtype: Any) -> str:
        """
        Categorize a caproto ChannelType into our type categories.

        Args:
            dtype: The caproto ChannelType or similar.

        Returns:
            Category string: "enum", "string", "integer", "float", or "unknown"
        """
        dtype_name = str(dtype).upper()

        if "ENUM" in dtype_name:
            return "enum"
        elif "STRING" in dtype_name or "CHAR" in dtype_name:
            return "string"
        elif any(t in dtype_name for t in ["SHORT", "INT", "LONG"]):
            return "integer"
        elif any(t in dtype_name for t in ["FLOAT", "DOUBLE"]):
            return "float"

        return "unknown"

    def _select_widget_type(self) -> None:
        """
        Select and create the appropriate widget based on PV characteristics.
        """
        widget_class = self._determine_widget_class()
        widget_type_name = widget_class.__name__

        if widget_type_name == self._current_widget_type:
            return

        if self._inner_widget is not None:
            self._inner_widget.setParent(None)
            self._inner_widget.deleteLater()
            self._inner_widget = None

        if self._placeholder is not None:
            self._placeholder.setParent(None)
            self._placeholder.deleteLater()
            self._placeholder = None

        is_readonly = self._readonly or (self._auto_readonly and self._is_array)

        if widget_class == PVComboBox:
            self._inner_widget = PVComboBox(parent=self)
            if self._pv and self._pv.metadata.get("enum_strings"):
                self._inner_widget.set_items(self._pv.metadata["enum_strings"])
        elif widget_class == PVLabel:
            self._inner_widget = PVLabel(parent=self)
        else:
            self._inner_widget = PVLineEdit(parent=self)

        self._inner_widget._pv_name = self._pv_name
        self._inner_widget._pv = self._pv
        self._inner_widget._connected = self._connected
        self._inner_widget._value = self._value
        self._inner_widget._readonly = is_readonly

        self._layout.addWidget(self._inner_widget)

        self._inner_widget._update_connection_style()
        self._inner_widget._update_readonly_state()
        self._inner_widget._update_display()

        self._current_widget_type = widget_type_name
        self.widget_type_changed.emit(widget_type_name)

    def _determine_widget_class(self) -> type:
        if self._pv_data_type == "enum" and not self._is_array:
            return PVComboBox
        if self._pv and self._pv.metadata.get("enum_strings") and not self._is_array:
            return PVComboBox
        if self._is_array:
            return PVLabel
        if self._readonly or self._prefer_label:
            return PVLabel
        return PVLineEdit

    def _update_display(self) -> None:
        if self._inner_widget is not None:
            self._inner_widget._update_display()

    def _get_widget_value(self) -> Any:
        if self._inner_widget is not None:
            return self._inner_widget._get_widget_value()
        return self._value

    def _set_widget_value(self, value: Any) -> None:
        self._value = value
        if self._inner_widget is not None:
            self._inner_widget._set_widget_value(value)

    def _update_readonly_state(self) -> None:
        if self._inner_widget is not None:
            is_readonly = self._readonly or (self._auto_readonly and self._is_array)
            self._inner_widget._readonly = is_readonly
            self._inner_widget._update_readonly_state()

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        data = {
            "auto_readonly": self._auto_readonly,
            "prefer_label": self._prefer_label,
            "current_widget_type": self._current_widget_type,
            "detected_pv_type": self._pv_data_type,
            "is_array": self._is_array,
        }
        if self._inner_widget is not None:
            data["inner_widget"] = self._inner_widget.get_introspection_data()
        return data

    def write_value(self, value: Any | None = None) -> None:
        if self._inner_widget is not None and hasattr(self._inner_widget, "write_value"):
            self._inner_widget.write_value(value)
        else:
            super().write_value(value)
