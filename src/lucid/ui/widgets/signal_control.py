"""Signal control widgets for direct device control.

Provides control UIs for ophyd signal devices:
- SignalControlWidget: Single signal control with read/write
- MultiSignalControlWidget: Monitor and control multiple signals together
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lucid.devices.model import DeviceCategory
from lucid.logbook import DeviceActionLogger
from lucid.ui.models.device_tree import DeviceTreeItem, NodeType
from lucid.ui.widgets.base_control import BaseControlWidget, register_control_widget
from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


def is_signal_item(item: DeviceTreeItem) -> bool:
    """Check if a DeviceTreeItem represents a signal device.

    Args:
        item: The tree item to check.

    Returns:
        True if the item is a signal device.
    """
    # Accept signal nodes directly
    if item.node_type == NodeType.SIGNAL:
        return True

    # Accept device nodes with signal category
    if item.node_type == NodeType.DEVICE:
        if item.device_info and item.device_info.category == DeviceCategory.DETECTOR:
            return True

        # Check ophyd object class name
        if item.ophyd_obj is not None:
            class_name = type(item.ophyd_obj).__name__.lower()
            if any(kw in class_name for kw in ("signal", "epicsignal")):
                return True

    return False


def _is_writable(ophyd_obj: Any) -> bool:
    """Check if an ophyd signal is writable.

    Args:
        ophyd_obj: The ophyd object to check.

    Returns:
        True if the signal supports put/set.
    """
    if ophyd_obj is None:
        return False

    # Check class name for common read-only types
    class_name = type(ophyd_obj).__name__
    if "ReadOnly" in class_name or "RO" in class_name:
        return False

    # Check for put or set methods
    return hasattr(ophyd_obj, "put") or hasattr(ophyd_obj, "set")


def _run_coroutine(coro: Any) -> Any:
    """Run an async coroutine from synchronous Qt code.

    Spawns a temporary event loop in a thread to avoid blocking
    or conflicting with Qt's event loop.

    Args:
        coro: Async coroutine to run.

    Returns:
        Result of the coroutine.
    """
    result = None
    exception = None

    def _run() -> None:
        nonlocal result, exception
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(coro)
        except Exception as e:
            exception = e
        finally:
            loop.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=2.0)

    if exception:
        raise exception
    return result


def _get_signal_value(ophyd_obj: Any) -> Any:
    """Get the current value of a signal.

    Handles both sync and async get() methods (e.g. BCSSignal).

    Args:
        ophyd_obj: The ophyd object to read.

    Returns:
        The current value, or None on error.
    """
    if ophyd_obj is None:
        return None

    try:
        if hasattr(ophyd_obj, "get"):
            val = ophyd_obj.get()
            # Handle async get() returning a coroutine
            if inspect.isawaitable(val):
                return _run_coroutine(val)
            return val
        if hasattr(ophyd_obj, "value"):
            return ophyd_obj.value
    except Exception:
        pass
    return None


def _put_signal_value(ophyd_obj: Any, value: Any) -> None:
    """Set a value on an ophyd signal, handling async put/set.

    Args:
        ophyd_obj: The ophyd object to write to.
        value: The value to set.
    """
    if ophyd_obj is None:
        return

    if hasattr(ophyd_obj, "put"):
        result = ophyd_obj.put(value)
        if inspect.isawaitable(result):
            _run_coroutine(result)
    elif hasattr(ophyd_obj, "set"):
        result = ophyd_obj.set(value)
        if inspect.isawaitable(result):
            _run_coroutine(result)


def _format_value(value: Any, precision: int = 4) -> str:
    """Format a signal value for display.

    Args:
        value: The value to format.
        precision: Decimal precision for floats.

    Returns:
        Formatted string.
    """
    if value is None:
        return "---"
    if isinstance(value, float):
        return f"{value:.{precision}f}"
    if isinstance(value, int):
        return str(value)
    return str(value)


class StatusDot(QWidget):
    """Small colored dot for connection/alarm status."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._color = "#666666"
        self._update_style()

    def set_color(self, color: str) -> None:
        """Set dot color."""
        self._color = color
        self._update_style()

    def set_connected(self, connected: bool) -> None:
        """Set connected/disconnected state."""
        self._color = "#4CAF50" if connected else "#F44336"
        self._update_style()

    def _update_style(self) -> None:
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {self._color};
                border-radius: 6px;
                border: 1px solid #333;
            }}
        """)


@register_control_widget
class SignalControlWidget(BaseControlWidget):
    """Control widget for a single signal device.

    Provides:
    - Current value readback display
    - Set value entry (if signal is writable)
    - Units display
    - Signal metadata (type, kind, description)
    - Connection status indicator

    Works directly with ophyd signal devices using their native interface.
    """

    display_name: ClassVar[str] = "Signal Control"
    priority: ClassVar[int] = 80  # Below motor priority

    def __init__(self, parent: QWidget | None = None) -> None:
        self._signal: Any = None
        self._signal_name: str = ""
        self._units: str = ""
        self._precision: int = 4
        self._writable: bool = False
        self._update_timer: QTimer | None = None
        self._device_info: Any = None  # DeviceInfo for connection tracking
        self._is_connecting: bool = False
        super().__init__(parent)

    @classmethod
    def can_control(cls, items: list[DeviceTreeItem]) -> bool:
        """Check if this widget can control the given items."""
        return len(items) == 1 and is_signal_item(items[0])

    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Set the signal to control."""
        self._items = items
        self._disconnect_connection_signals()

        if items and len(items) == 1:
            item = items[0]
            self._signal = item.ophyd_obj
            self._signal_name = item.name
            self._device_info = item.device_info
            self._writable = _is_writable(self._signal)

            # Get units and precision from metadata
            if item.device_info and item.device_info.metadata:
                self._units = item.device_info.metadata.get("units", "")
                self._precision = item.device_info.metadata.get("precision", 4)
            else:
                self._units = self._get_units_from_signal()

            # Check if device is connecting or needs connection
            if self._signal is None and item.device_info:
                from lucid.devices.model import DeviceStatus

                state = item.device_info._state
                if state and state.status == DeviceStatus.CONNECTING:
                    self._is_connecting = True
                    self._show_connecting_state()
                    self._connect_connection_signals()
                    return
                elif state and state.status in (DeviceStatus.UNKNOWN, DeviceStatus.OFFLINE):
                    # Try on-demand connection
                    self._request_connection()
                    return

            self._is_connecting = False
            self._update_display()
            self._update_writable_ui()
            self._start_updates()
        else:
            self._signal = None
            self._signal_name = ""
            self._device_info = None
            self._writable = False
            self._is_connecting = False
            self._stop_updates()
            self._clear_display()

    def _connect_connection_signals(self) -> None:
        """Connect to DeviceCatalog signals for connection updates."""
        try:
            from lucid.devices import DeviceCatalog

            catalog = DeviceCatalog.get_instance()
            catalog.device_connected.connect(self._on_device_connected)
            catalog.device_connection_failed.connect(self._on_device_connection_failed)
        except Exception:
            pass

    def _disconnect_connection_signals(self) -> None:
        """Disconnect from DeviceCatalog signals."""
        try:
            from lucid.devices import DeviceCatalog

            catalog = DeviceCatalog.get_instance()
            catalog.device_connected.disconnect(self._on_device_connected)
            catalog.device_connection_failed.disconnect(self._on_device_connection_failed)
        except Exception:
            pass

    def _request_connection(self) -> None:
        """Request on-demand connection for the current device."""
        if self._device_info is None:
            self._show_no_connection()
            return

        try:
            from lucid.devices import DeviceCatalog

            catalog = DeviceCatalog.get_instance()
            if catalog.request_device_connection(self._device_info.id):
                self._is_connecting = True
                self._show_connecting_state()
                self._connect_connection_signals()
            else:
                self._show_no_connection()
        except Exception as e:
            logger.warning("Failed to request device connection: {}", e)
            self._show_no_connection()

    @Slot(str)
    def _on_device_connected(self, device_id_str: str) -> None:
        """Handle device connected signal."""
        if self._device_info is None:
            return
        if device_id_str != str(self._device_info.id):
            return

        # Device is now connected — update our reference
        self._signal = self._device_info._ophyd_device
        self._writable = _is_writable(self._signal)
        self._is_connecting = False
        self._disconnect_connection_signals()
        self._update_display()
        self._update_writable_ui()
        self._start_updates()
        logger.debug("Signal '{}' connected, controls enabled", self._signal_name)

    @Slot(str, str)
    def _on_device_connection_failed(self, device_id_str: str, error: str) -> None:
        """Handle device connection failed signal."""
        if self._device_info is None:
            return
        if device_id_str != str(self._device_info.id):
            return

        self._is_connecting = False
        self._disconnect_connection_signals()
        self._show_connection_failed(error)

    def _show_connecting_state(self) -> None:
        """Show UI state while device is connecting."""
        self._name_label.setText(f"{self._signal_name} (Connecting...)")
        self._value_display.setText("...")
        self._status_dot.set_color("#FFC107")  # Yellow/warning
        self._status_label.setText("Connecting")
        self._set_controls_enabled(False)

    def _show_no_connection(self) -> None:
        """Show UI state when device cannot be connected."""
        self._name_label.setText(f"{self._signal_name} (Not Connected)")
        self._value_display.setText("---")
        self._status_dot.set_connected(False)
        self._status_label.setText("Unavailable")
        self._set_controls_enabled(False)

    def _show_connection_failed(self, error: str) -> None:
        """Show UI state when connection failed."""
        self._name_label.setText(f"{self._signal_name} (Connection Failed)")
        self._value_display.setText("---")
        self._status_dot.set_connected(False)
        self._status_label.setText("Failed")
        self._set_controls_enabled(False)

    def _get_units_from_signal(self) -> str:
        """Try to get units from the ophyd signal metadata."""
        if self._signal is None:
            return ""
        try:
            if hasattr(self._signal, "metadata") and isinstance(self._signal.metadata, dict):
                return self._signal.metadata.get("units", "")
            if hasattr(self._signal, "describe"):
                desc = self._signal.describe()
                if inspect.isawaitable(desc):
                    desc = _run_coroutine(desc)
                if desc:
                    # describe() returns {name: {shape, dtype, units, ...}}
                    for _key, info in desc.items():
                        if isinstance(info, dict):
                            return info.get("units", "")
        except Exception:
            pass
        return ""

    def _setup_ui(self) -> None:
        """Setup the signal control UI."""
        # Signal name header
        self._name_label = QLabel("No Signal Selected")
        self._name_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        self._layout.addWidget(self._name_label)

        # Status bar
        status_layout = QHBoxLayout()
        self._status_dot = StatusDot()
        self._status_label = QLabel("Disconnected")
        self._type_label = QLabel("")
        self._type_label.setStyleSheet("color: #888; font-style: italic;")
        status_layout.addWidget(self._status_dot)
        status_layout.addWidget(self._status_label)
        status_layout.addStretch()
        status_layout.addWidget(self._type_label)
        self._layout.addLayout(status_layout)

        # Value group
        val_group = QGroupBox("Value")
        val_layout = QGridLayout(val_group)

        # Current value display
        val_layout.addWidget(QLabel("Current:"), 0, 0)
        self._value_display = QLabel("---")
        self._value_display.setStyleSheet("""
            QLabel {
                font-size: 16pt;
                font-weight: bold;
                font-family: monospace;
                padding: 4px 8px;
            }
        """)
        val_layout.addWidget(self._value_display, 0, 1)
        self._units_label = QLabel("")
        val_layout.addWidget(self._units_label, 0, 2)

        # Set value entry (shown only for writable signals)
        self._set_label = QLabel("Set:")
        val_layout.addWidget(self._set_label, 1, 0)
        self._set_edit = QLineEdit()
        self._set_edit.setPlaceholderText("Enter value")
        self._set_edit.returnPressed.connect(self._on_set_clicked)
        val_layout.addWidget(self._set_edit, 1, 1)

        self._set_btn = QPushButton("Set")
        self._set_btn.setFixedWidth(50)
        self._set_btn.clicked.connect(self._on_set_clicked)
        val_layout.addWidget(self._set_btn, 1, 2)

        self._layout.addWidget(val_group)

        # Info group
        info_group = QGroupBox("Info")
        info_layout = QGridLayout(info_group)

        info_layout.addWidget(QLabel("Kind:"), 0, 0)
        self._kind_label = QLabel("---")
        info_layout.addWidget(self._kind_label, 0, 1)

        info_layout.addWidget(QLabel("Access:"), 1, 0)
        self._access_label = QLabel("---")
        info_layout.addWidget(self._access_label, 1, 1)

        info_layout.addWidget(QLabel("Type:"), 2, 0)
        self._dtype_label = QLabel("---")
        info_layout.addWidget(self._dtype_label, 2, 1)

        self._layout.addWidget(info_group)

        self._layout.addStretch()

        # Initial state - disabled
        self._set_controls_enabled(False)

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable control widgets."""
        self._set_edit.setEnabled(enabled and self._writable)
        self._set_btn.setEnabled(enabled and self._writable)

    def _update_writable_ui(self) -> None:
        """Show/hide set controls based on writability."""
        self._set_label.setVisible(self._writable)
        self._set_edit.setVisible(self._writable)
        self._set_btn.setVisible(self._writable)
        self._access_label.setText("Read/Write" if self._writable else "Read Only")

    def _start_updates(self) -> None:
        """Start periodic value updates."""
        if self._update_timer is None:
            self._update_timer = QTimer(self)
            self._update_timer.timeout.connect(self._update_display)
        self._update_timer.start(500)  # 2 Hz updates (signals change less often)

    def _stop_updates(self) -> None:
        """Stop periodic value updates."""
        if self._update_timer is not None:
            self._update_timer.stop()

    def _update_display(self) -> None:
        """Update the value and status display."""
        if self._signal is None:
            return

        self._name_label.setText(self._signal_name)
        self._units_label.setText(self._units)
        self._set_controls_enabled(True)

        try:
            # Get current value
            value = _get_signal_value(self._signal)
            self._value_display.setText(_format_value(value, self._precision))

            # Update connection status
            connected = True
            if hasattr(self._signal, "connected"):
                connected = bool(self._signal.connected)

            self._status_dot.set_connected(connected)
            self._status_label.setText("Connected" if connected else "Disconnected")

            # Update type info
            class_name = type(self._signal).__name__
            self._type_label.setText(class_name)

            # Update kind info
            if hasattr(self._signal, "kind"):
                self._kind_label.setText(self._signal.kind.name)

            # Update dtype
            if value is not None:
                self._dtype_label.setText(type(value).__name__)

        except Exception as e:
            logger.warning("Error updating signal display: {}", e)

    def _clear_display(self) -> None:
        """Clear the display when no signal is selected."""
        self._name_label.setText("No Signal Selected")
        self._value_display.setText("---")
        self._units_label.setText("")
        self._status_dot.set_color("#666666")
        self._status_label.setText("Disconnected")
        self._type_label.setText("")
        self._kind_label.setText("---")
        self._access_label.setText("---")
        self._dtype_label.setText("---")
        self._set_controls_enabled(False)

    @Slot()
    def _on_set_clicked(self) -> None:
        """Set the signal value."""
        if self._signal is None or not self._writable:
            return

        text = self._set_edit.text().strip()
        if not text:
            return

        try:
            # Try to coerce value to the appropriate type
            current = _get_signal_value(self._signal)
            if isinstance(current, float):
                new_value = float(text)
            elif isinstance(current, int):
                new_value = int(text)
            else:
                new_value = text

            old_value = current

            # Record action
            action_logger = DeviceActionLogger.get_instance()
            action_logger.record_action(
                device_name=self._signal_name,
                action_type="set",
                old_value=old_value,
                new_value=new_value,
                unit=self._units,
            )

            # Perform the set (handle async put/set)
            _put_signal_value(self._signal, new_value)

            logger.info("Set {} to {}", self._signal_name, new_value)

        except ValueError:
            self.control_error.emit(f"Invalid value: {text}")
        except Exception as e:
            self.control_error.emit(f"Set failed: {e}")
            logger.error("Signal set failed: {}", e)

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        self._stop_updates()
        super().closeEvent(event)


class SignalRowWidget(QWidget):
    """Individual signal row for multi-signal control."""

    set_requested = Signal(str, str)  # name, value_text

    def __init__(
        self,
        name: str,
        signal_obj: Any,
        item: DeviceTreeItem,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.name = name
        self.signal_obj = signal_obj
        self.item = item
        self._precision = 4
        self._writable = _is_writable(signal_obj)

        if item.device_info and item.device_info.metadata:
            self._precision = item.device_info.metadata.get("precision", 4)

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # Status dot
        self._status_dot = StatusDot()
        layout.addWidget(self._status_dot)

        # Signal name
        self._name_label = QLabel(self.name)
        self._name_label.setStyleSheet("font-weight: bold;")
        self._name_label.setMinimumWidth(100)
        layout.addWidget(self._name_label)

        # Value display
        self._value_label = QLabel("---")
        self._value_label.setStyleSheet("font-family: monospace; font-size: 11pt;")
        self._value_label.setMinimumWidth(100)
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._value_label)

        # Units
        units = ""
        if self.item.device_info and self.item.device_info.metadata:
            units = self.item.device_info.metadata.get("units", "")
        self._units_label = QLabel(units)
        self._units_label.setMinimumWidth(30)
        layout.addWidget(self._units_label)

        # Spacer
        layout.addSpacing(16)

        # Set value entry (only for writable signals)
        self._set_edit = QLineEdit()
        self._set_edit.setMaximumWidth(100)
        self._set_edit.setPlaceholderText("Value")
        self._set_edit.returnPressed.connect(self._on_set)
        self._set_edit.setVisible(self._writable)
        layout.addWidget(self._set_edit)

        self._set_btn = QPushButton("Set")
        self._set_btn.setFixedWidth(40)
        self._set_btn.clicked.connect(self._on_set)
        self._set_btn.setVisible(self._writable)
        layout.addWidget(self._set_btn)

        if not self._writable:
            # Add a read-only indicator
            ro_label = QLabel("RO")
            ro_label.setStyleSheet("color: #888; font-size: 9pt;")
            layout.addWidget(ro_label)

        layout.addStretch()

    def update_display(self) -> None:
        """Update value and status display."""
        try:
            value = _get_signal_value(self.signal_obj)
            self._value_label.setText(_format_value(value, self._precision))

            connected = True
            if hasattr(self.signal_obj, "connected"):
                connected = bool(self.signal_obj.connected)
            self._status_dot.set_connected(connected)
        except Exception:
            pass

    @Slot()
    def _on_set(self) -> None:
        """Handle Set button."""
        text = self._set_edit.text().strip()
        if text:
            self.set_requested.emit(self.name, text)


@register_control_widget
class MultiSignalControlWidget(BaseControlWidget):
    """Control widget for multiple signal devices.

    Displays individual signal rows with:
    - Value readback and connection status per signal
    - Set value entry for writable signals
    - Read-only indicator for non-writable signals
    """

    display_name: ClassVar[str] = "Multi-Signal Control"
    priority: ClassVar[int] = 70  # Below single signal

    def __init__(self, parent: QWidget | None = None) -> None:
        self._signals: list[tuple[str, Any, DeviceTreeItem]] = []
        self._signal_rows: list[SignalRowWidget] = []
        self._update_timer: QTimer | None = None
        super().__init__(parent)

    @classmethod
    def can_control(cls, items: list[DeviceTreeItem]) -> bool:
        """Check if this widget can control the given items."""
        if len(items) < 2:
            return False
        return all(is_signal_item(item) for item in items)

    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Set the signals to control."""
        self._items = items
        self._signals = []

        for item in items:
            if is_signal_item(item) and item.ophyd_obj is not None:
                self._signals.append((item.name, item.ophyd_obj, item))

        self._rebuild_signal_list()
        if self._signals:
            self._start_updates()
        else:
            self._stop_updates()

    def _setup_ui(self) -> None:
        """Setup the multi-signal control UI."""
        # Header
        header_layout = QHBoxLayout()
        header_label = QLabel("Multi-Signal Control")
        header_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        header_layout.addWidget(header_label)
        header_layout.addStretch()

        # Signal count
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #888;")
        header_layout.addWidget(self._count_label)

        self._layout.addLayout(header_layout)

        # Scrollable container for signal rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        self._signals_container = QWidget()
        self._signals_layout = QVBoxLayout(self._signals_container)
        self._signals_layout.setContentsMargins(0, 0, 0, 0)
        self._signals_layout.setSpacing(2)
        self._signals_layout.addStretch()

        scroll.setWidget(self._signals_container)
        self._layout.addWidget(scroll)

    def _rebuild_signal_list(self) -> None:
        """Rebuild the signal row widgets."""
        # Clear existing rows
        for row in self._signal_rows:
            row.deleteLater()
        self._signal_rows.clear()

        # Remove stretch if present
        while self._signals_layout.count() > 0:
            item = self._signals_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Create new rows
        for name, sig, item in self._signals:
            row = SignalRowWidget(name, sig, item, self._signals_container)
            row.set_requested.connect(self._on_set_requested)
            self._signals_layout.addWidget(row)
            self._signal_rows.append(row)

        self._signals_layout.addStretch()

        # Update count
        writable_count = sum(1 for _, s, _ in self._signals if _is_writable(s))
        self._count_label.setText(f"{len(self._signals)} signals ({writable_count} writable)")

    def _start_updates(self) -> None:
        """Start periodic updates."""
        if self._update_timer is None:
            self._update_timer = QTimer(self)
            self._update_timer.timeout.connect(self._update_display)
        self._update_timer.start(500)

    def _stop_updates(self) -> None:
        """Stop periodic updates."""
        if self._update_timer is not None:
            self._update_timer.stop()

    def _update_display(self) -> None:
        """Update all signal row displays."""
        for row in self._signal_rows:
            row.update_display()

    @Slot(str, str)
    def _on_set_requested(self, name: str, value_text: str) -> None:
        """Handle set request from a signal row."""
        sig = None
        item = None
        for n, s, i in self._signals:
            if n == name:
                sig = s
                item = i
                break

        if sig is None:
            return

        try:
            # Coerce value type
            current = _get_signal_value(sig)
            if isinstance(current, float):
                new_value = float(value_text)
            elif isinstance(current, int):
                new_value = int(value_text)
            else:
                new_value = value_text

            # Get units
            unit = ""
            if item and item.device_info and item.device_info.metadata:
                unit = item.device_info.metadata.get("units", "")

            # Record action
            action_logger = DeviceActionLogger.get_instance()
            action_logger.record_action(
                device_name=name,
                action_type="set",
                old_value=current,
                new_value=new_value,
                unit=unit,
            )

            # Perform the set (handle async put/set)
            _put_signal_value(sig, new_value)

            logger.info("Set {} to {}", name, new_value)

        except ValueError:
            self.control_error.emit(f"Invalid value for {name}: {value_text}")
        except Exception as e:
            self.control_error.emit(f"Set failed for {name}: {e}")
            logger.error("Signal set failed for {}: {}", name, e)

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        self._stop_updates()
        super().closeEvent(event)
