"""Base class for ophyd signal widgets.

Parallel to EpicsWidget (which wraps caproto PVs), this base class
wraps ophyd signals -- providing subscription, thread-safe UI updates,
and a uniform interface for widgets that control ophyd devices.
"""
from __future__ import annotations

import inspect
from abc import abstractmethod
from typing import Any, ClassVar

from PySide6.QtCore import Property, QEvent, QTimer, Signal, Slot
from PySide6.QtWidgets import QLabel, QWidget

from lucid.epics.widgets.style import WidgetStyles


class OphydWidget(QWidget):
    """Abstract base class for widgets bound to ophyd signals.

    This class provides:
    - Automatic signal subscription management
    - Thread-safe UI updates via invoke_in_main_thread
    - Polling fallback if subscription fails
    - Consistent styling for connection states
    - Introspection API matching EpicsWidget

    Subclasses must implement:
    - _update_display(): Update the widget from the current value
    - _get_widget_value(): Get the current value from the widget UI
    - _set_widget_value(): Set the widget to display a specific value

    Signals:
        value_changed: Emitted when the displayed value changes.
        connection_changed: Emitted when connection state changes.

    Class Attributes:
        widget_type: A human-readable type name for introspection.
        widget_description: A description of what this widget does.
    """

    widget_type: ClassVar[str] = "OphydWidget"
    widget_description: ClassVar[str] = "Base class for ophyd signal widgets"

    value_changed = Signal(object)
    connection_changed = Signal(bool)

    def __init__(
        self,
        signal: Any = None,
        parent: QWidget | None = None,
        readonly: bool = False,
        show_units: bool = True,
    ) -> None:
        super().__init__(parent)
        self._signal: Any = None
        self._sub_id: int | None = None
        self._readonly = readonly
        self._value: Any = None
        self._connected = False
        self._poll_timer: QTimer | None = None

        # Units display — the QLabel is lazily created; subclasses call
        # ``_ensure_units_label()`` and add the returned widget to their
        # own layout if they want units shown alongside the value.
        self._show_units = show_units
        self._units: str = ""
        self._units_label: QLabel | None = None

        # Apply base styling
        self._update_connection_style()

        if signal is not None:
            self.signal = signal

    # -- Qt Properties --------------------------------------------------------

    @Property(bool)
    def readonly(self) -> bool:
        """Whether this widget is read-only."""
        return self._readonly

    @readonly.setter
    def readonly(self, value: bool) -> None:
        self._readonly = value
        self._update_readonly_state()

    @Property(bool, notify=connection_changed)
    def connected(self) -> bool:
        """Whether the signal is currently connected."""
        return self._connected

    @Property(bool)
    def show_units(self) -> bool:
        """Whether to display units next to the value."""
        return self._show_units

    @show_units.setter
    def show_units(self, value: bool) -> None:
        self._show_units = value
        if self._units_label is not None:
            self._units_label.setVisible(value and bool(self._units))

    def _ensure_units_label(self) -> QLabel:
        """Return the units QLabel, creating it on first access.

        Subclasses call this during layout construction and add the
        returned label to their own layout. The label is created hidden
        and becomes visible once a non-empty units string is fetched.
        """
        if self._units_label is None:
            self._units_label = QLabel()
            self._units_label.setObjectName("units_label")
            self._units_label.setVisible(False)
        return self._units_label

    # -- Signal binding -------------------------------------------------------

    @property
    def signal(self) -> Any:
        """The ophyd signal this widget is bound to."""
        return self._signal

    @signal.setter
    def signal(self, sig: Any) -> None:
        self._disconnect_signal()
        self._signal = sig
        self.setToolTip(self._signal_display_name(sig))
        if sig is not None:
            self._connect_signal()

    @staticmethod
    def _signal_display_name(sig: Any) -> str:
        """Return the PV name for an EPICS-backed signal, falling back to the
        ophyd dotted name for simulated/soft signals that have no PV.
        """
        if sig is None:
            return ""
        pvname = getattr(sig, "pvname", None)
        if isinstance(pvname, str) and pvname:
            return pvname
        # ``source`` is defined on every ophyd Signal — for EPICS it is
        # ``PV:<pvname>``; for sim signals it is ``SIM:<name>`` etc.
        source = getattr(sig, "source", None)
        if isinstance(source, str) and source:
            return source[3:] if source.startswith("PV:") else source
        name = getattr(sig, "name", "")
        return name if isinstance(name, str) else ""

    # -- Tooltip forwarding ---------------------------------------------------
    #
    # Qt shows a widget's tooltip only when that specific widget is hovered;
    # children (the inner QLabel, QLineEdit, QDoubleSpinBox...) sit on top
    # and do NOT reliably propagate ToolTip events up to us. We install
    # ourselves as an event filter on every child so that any ToolTip event
    # on a child whose own tooltip is empty is answered with our PV tooltip.

    def childEvent(self, event: Any) -> None:
        super().childEvent(event)
        if event.type() == QEvent.Type.ChildAdded:
            child = event.child()
            if isinstance(child, QWidget):
                child.installEventFilter(self)

    def eventFilter(self, obj: Any, event: Any) -> bool:
        if (
            event.type() == QEvent.Type.ToolTip
            and isinstance(obj, QWidget)
            and obj is not self
            and not obj.toolTip()
        ):
            tip = self.toolTip()
            if tip:
                from PySide6.QtWidgets import QToolTip

                try:
                    pos = event.globalPos()
                except AttributeError:
                    pos = event.globalPosition().toPoint()
                QToolTip.showText(pos, tip, obj)
                return True
        return super().eventFilter(obj, event)

    def _connect_signal(self) -> None:
        """Subscribe to the ophyd signal and read the initial value."""
        if self._signal is None:
            return

        # Determine connection state
        if hasattr(self._signal, "connected"):
            self._connected = bool(self._signal.connected)
        else:
            self._connected = True
        self._update_connection_style()
        self._update_readonly_state()
        self.connection_changed.emit(self._connected)

        # Subscribe for value updates
        try:
            self._sub_id = self._signal.subscribe(self._on_signal_value)
        except Exception:
            self._start_polling()

        # Read current value
        self._read_initial_value()

        # Pull engineering units from the signal (best-effort)
        self._fetch_units()

    def _fetch_units(self) -> None:
        """Extract engineering units from the ophyd signal.

        Checks ``signal.metadata["units"]`` first, then falls back to
        ``signal.describe()`` which some signal types populate lazily.
        The result is stored in ``self._units`` and applied to
        ``self._units_label`` if a subclass has created one.
        """
        if self._signal is None:
            return
        units = ""
        try:
            meta = getattr(self._signal, "metadata", None)
            if isinstance(meta, dict):
                units = meta.get("units", "") or ""
            if not units and hasattr(self._signal, "describe"):
                desc = self._signal.describe()
                if inspect.isawaitable(desc):
                    desc = None
                if desc:
                    for _key, info in desc.items():
                        if isinstance(info, dict):
                            units = info.get("units", "") or ""
                            if units:
                                break
        except Exception:
            units = ""
        self._units = units
        if self._units_label is not None:
            self._units_label.setText(units)
            self._units_label.setVisible(self._show_units and bool(units))

    def _disconnect_signal(self) -> None:
        """Unsubscribe and release the ophyd signal."""
        self._stop_polling()
        if self._signal is not None and self._sub_id is not None:
            try:
                self._signal.unsubscribe(self._sub_id)
            except Exception:
                pass
        self._sub_id = None
        self._signal = None
        self._connected = False
        self._update_connection_style()
        self._update_readonly_state()

        # Clear units display so it doesn't stick around after rebinding.
        self._units = ""
        if self._units_label is not None:
            self._units_label.setText("")
            self._units_label.setVisible(False)

    # -- Value updates --------------------------------------------------------

    @staticmethod
    def _coerce_scalar(value: Any) -> Any:
        """Extract a Python scalar from numpy/array-like/namedtuple values.

        Ophyd callbacks and ``.get()`` can deliver numpy scalars,
        single-element arrays, or named tuples (e.g. SynGaussTuple).
        Coerce them to plain Python types so that ``isinstance`` checks
        and ``str()`` behave predictably in downstream formatting.
        """
        if value is None:
            return None

        # Named tuples from simulated devices (e.g. SynGaussTuple) —
        # extract the first field which holds the actual value.
        if hasattr(value, "_fields") and value._fields:
            value = getattr(value, value._fields[0])

        # bytes from EPICS string PVs → decode
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")

        # Single-element sequences → extract scalar
        if hasattr(value, "__len__") and not isinstance(value, str):
            try:
                if len(value) == 1:
                    value = value[0]
            except TypeError:
                # 0-d numpy array: ndim == 0, has __len__ but len() raises
                pass

        # numpy scalar → native Python type
        if hasattr(value, "item"):
            try:
                value = value.item()
            except (ValueError, AttributeError):
                pass

        return value

    def _on_signal_value(self, value: Any = None, **kwargs: Any) -> None:
        """Ophyd subscription callback -- runs on a background thread."""
        from lucid.utils.threads import invoke_in_main_thread

        self._value = self._coerce_scalar(value)
        invoke_in_main_thread(self._apply_value_update)

    @Slot()
    def _apply_value_update(self) -> None:
        """Apply the latest value on the Qt main thread."""
        self._update_display()
        self.value_changed.emit(self._value)

    def _read_initial_value(self) -> None:
        """Read the current signal value synchronously."""
        if self._signal is None:
            return
        try:
            val = self._signal.get()
            if inspect.isawaitable(val):
                return  # async signals are not read synchronously
            self._value = self._coerce_scalar(val)
            self._update_display()
            self.value_changed.emit(self._value)
        except Exception:
            pass

    # -- Polling fallback -----------------------------------------------------

    def _start_polling(self) -> None:
        """Start a 500 ms poll timer (fallback when subscribe fails)."""
        if self._poll_timer is None:
            self._poll_timer = QTimer(self)
            self._poll_timer.timeout.connect(self._poll_value)
        self._poll_timer.start(500)

    def _stop_polling(self) -> None:
        """Stop the poll timer if running."""
        if self._poll_timer is not None:
            self._poll_timer.stop()

    @Slot()
    def _poll_value(self) -> None:
        """Timer callback: re-read the signal value."""
        self._read_initial_value()

    # -- Writing --------------------------------------------------------------

    def write_value(self, value: Any | None = None) -> None:
        """Write a value to the ophyd signal.

        Args:
            value: The value to write. If None, uses the current widget value.

        Raises:
            RuntimeError: If the widget is readonly or signal is not connected.
        """
        if self._readonly:
            raise RuntimeError("Widget is readonly")
        if self._signal is None or not self._connected:
            raise RuntimeError("Signal is not connected")

        if value is None:
            value = self._get_widget_value()

        if hasattr(self._signal, "put"):
            result = self._signal.put(value)
            if inspect.isawaitable(result):
                import asyncio
                import threading

                def _run() -> None:
                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(result)
                    finally:
                        loop.close()

                threading.Thread(target=_run, daemon=True).start()
        elif hasattr(self._signal, "set"):
            self._signal.set(value)

    # -- Styling --------------------------------------------------------------

    def _update_connection_style(self) -> None:
        """Update widget styling based on connection state."""
        if self._connected:
            self.setStyleSheet(WidgetStyles.connected())
        else:
            self.setStyleSheet(WidgetStyles.disconnected())

    def _update_readonly_state(self) -> None:
        """Update widget state for readonly changes. Override in subclasses."""
        pass

    # -- Abstract interface ---------------------------------------------------

    @abstractmethod
    def _update_display(self) -> None:
        """Update the widget display to reflect the current signal value."""
        pass

    @abstractmethod
    def _get_widget_value(self) -> Any:
        """Get the current value from the widget's UI."""
        pass

    @abstractmethod
    def _set_widget_value(self, value: Any) -> None:
        """Set the widget's UI to display a specific value."""
        pass

    # -- Introspection --------------------------------------------------------

    def get_introspection_data(self) -> dict[str, Any]:
        """Get comprehensive introspection data for this widget."""
        data = {
            "widget_type": self.widget_type,
            "widget_description": self.widget_description,
            "object_name": self.objectName(),
            "class_name": self.__class__.__name__,
            "signal_name": getattr(self._signal, "name", None),
            "connected": self._connected,
            "current_value": self._value,
            "value_type": type(self._value).__name__ if self._value is not None else None,
            "units": self._units,
            "show_units": self._show_units,
            "readonly": self._readonly,
            "enabled": self.isEnabled(),
            "visible": self.isVisible(),
            "geometry": {
                "x": self.x(),
                "y": self.y(),
                "width": self.width(),
                "height": self.height(),
            },
        }
        data.update(self._get_specific_introspection_data())
        return data

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Override in subclasses for widget-specific introspection."""
        return {}

    @classmethod
    def get_class_introspection_data(cls) -> dict[str, Any]:
        """Get class-level introspection data."""
        return {
            "widget_type": cls.widget_type,
            "widget_description": cls.widget_description,
            "class_name": cls.__name__,
            "module": cls.__module__,
        }

    # -- Lifecycle ------------------------------------------------------------

    def closeEvent(self, event: Any) -> None:
        """Clean up signal subscription on close."""
        self._disconnect_signal()
        super().closeEvent(event)
