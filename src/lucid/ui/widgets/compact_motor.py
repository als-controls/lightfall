"""Compact motor control widget for favorites display.

Provides a single horizontal row with motor name, readback,
jog/abs toggle, setpoint entry, go button, and stop button.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import qtawesome as qta
from PySide6.QtCore import QPoint, QSize, Qt, Signal, Slot
from PySide6.QtGui import QDoubleValidator, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QWidget,
)

from lucid.epics.widgets.ophyd_label import OphydLabel
from lucid.epics.widgets.ophyd_lineedit import OphydLineEdit
from lucid.epics.widgets.status_indicator import StatusIndicator
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.devices.model import DeviceInfo


class _ElidedLabel(QLabel):
    """QLabel that draws its text with a right-side ellipsis when the
    rendered text is wider than the label. Keeps the full text available
    for the tooltip and accessibility."""

    def paintEvent(self, event: Any) -> None:  # type: ignore[override]
        painter = QPainter(self)
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(
            self.text(), Qt.TextElideMode.ElideRight, self.width()
        )
        painter.drawText(self.rect(), int(self.alignment()), elided)


class CompactMotorWidget(QWidget):
    """Compact single-row motor control widget.

    Layout (left to right):
        Name | Readback | Jog/Abs toggle | Setpoint | Go | Stop

    Signals:
        open_controller_requested: Emitted with device_id when user wants full controller tab.
        remove_favorite_requested: Emitted with device_id when user wants to unfavorite.
        control_error: Emitted with error message string.
    """

    open_controller_requested = Signal(str)
    remove_favorite_requested = Signal(str)
    control_error = Signal(str)

    WIDGET_HEIGHT = 42
    _BUTTON_STYLE = "QPushButton { padding: 2px 6px; font-size: 10pt; }"

    def __init__(
        self,
        device_info: DeviceInfo,
        ophyd_obj: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._device_info = device_info
        self._motor = ophyd_obj
        self._is_jog_mode = False
        self._moving_sub_id: int | None = None
        self._moving_sub_signal: Any = None

        self.setFixedHeight(self.WIDGET_HEIGHT)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self._setup_ui()
        self._bind_signals()
        self._update_state()

    @property
    def device_id(self) -> str:
        # Returns the device name — the stable identifier favorites pass
        # around. DeviceInfo.id is a per-session UUID and not safe to
        # persist or route across catalog rebuilds.
        return self._device_info.name

    @property
    def is_jog_mode(self) -> bool:
        return self._is_jog_mode

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        self._status_indicator = StatusIndicator(size=10)
        self._status_indicator.setToolTip("Motor status")
        layout.addWidget(self._status_indicator)

        self._name_label = _ElidedLabel(self._device_info.name)
        self._name_label.setStyleSheet("font-weight: bold;")
        self._name_label.setFixedWidth(120)
        self._name_label.setToolTip(self._device_info.name)
        layout.addWidget(self._name_label)

        precision = 4
        if self._device_info.metadata:
            precision = self._device_info.metadata.get("precision", 4)
        units = self._resolve_units()
        # show_units=False on OphydLabel — its units label is signal-driven
        # and disappears when EGU isn't reported. We render units ourselves
        # via _resolve_units() so they always show.
        self._rbv_display = OphydLabel(precision=precision, show_units=False)
        self._rbv_display._value_label.setStyleSheet(
            "font-family: monospace; font-size: 10pt;"
        )
        self._rbv_display._value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._rbv_display.setMinimumWidth(56)
        layout.addWidget(self._rbv_display)

        self._units_label = QLabel(units)
        self._units_label.setStyleSheet("color: #9aa0a6; font-size: 10pt;")
        self._units_label.setFixedWidth(28)
        layout.addWidget(self._units_label)

        self._mode_btn = QPushButton("Abs")
        self._mode_btn.setFixedWidth(56)
        self._mode_btn.setStyleSheet(self._BUTTON_STYLE)
        self._mode_btn.setToolTip("Toggle between Absolute and Jog (relative) mode")
        self._mode_btn.setCheckable(True)
        self._mode_btn.clicked.connect(self._on_mode_toggled)
        layout.addWidget(self._mode_btn)

        self._jog_left_btn = QPushButton()
        self._jog_left_btn.setIcon(
            qta.icon("ph.arrow-fat-lines-left-fill", color="#90caf9")
        )
        self._jog_left_btn.setIconSize(QSize(18, 18))
        self._jog_left_btn.setFixedWidth(40)
        self._jog_left_btn.setStyleSheet(self._BUTTON_STYLE)
        self._jog_left_btn.setToolTip("Jog negative by step")
        self._jog_left_btn.setVisible(False)
        self._jog_left_btn.clicked.connect(self._on_jog_left_clicked)
        layout.addWidget(self._jog_left_btn)

        # Two stacked entries in the same slot:
        # - Abs mode: OphydLineEdit bound to user_setpoint shows the live
        #   setpoint. Writes go through motor.set() via the go button, so
        #   OphydLineEdit's own write paths are disabled.
        # - Jog mode: a plain QLineEdit defaulting to "1". A separate
        #   widget avoids the readonly/disconnected styling OphydLineEdit
        #   applies when its signal is unbound.
        self._setpoint_edit = OphydLineEdit(
            precision=precision,
            show_units=False,
            write_on_enter=False,
            write_on_focus_out=False,
        )
        self._setpoint_edit._line_edit.setValidator(QDoubleValidator())
        self._setpoint_edit._line_edit.setPlaceholderText("Target")
        self._setpoint_edit.setMaximumWidth(100)
        self._setpoint_edit._line_edit.returnPressed.connect(self._on_go_clicked)
        layout.addWidget(self._setpoint_edit)

        self._jog_edit = QLineEdit("1")
        self._jog_edit.setValidator(QDoubleValidator())
        self._jog_edit.setPlaceholderText("Step")
        self._jog_edit.setMaximumWidth(100)
        self._jog_edit.returnPressed.connect(self._on_go_clicked)
        self._jog_edit.setVisible(False)
        layout.addWidget(self._jog_edit)

        # _go_btn doubles as "go to target" in abs mode and "jog positive" in jog mode.
        self._go_btn = QPushButton()
        self._go_btn.setIcon(qta.icon("ph.arrow-fat-right-fill", color="#90caf9"))
        self._go_btn.setIconSize(QSize(18, 18))
        self._go_btn.setFixedWidth(40)
        self._go_btn.setStyleSheet(self._BUTTON_STYLE)
        self._go_btn.setToolTip("Move to target")
        self._go_btn.clicked.connect(self._on_go_clicked)
        layout.addWidget(self._go_btn)

        self._stop_btn = QPushButton()
        self._stop_btn.setIcon(qta.icon("mdi6.stop", color="#F44336"))
        self._stop_btn.setIconSize(QSize(18, 18))
        self._stop_btn.setFixedWidth(36)
        self._stop_btn.setStyleSheet(self._BUTTON_STYLE)
        self._stop_btn.setToolTip("Stop motor")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self._stop_btn)

    def _resolve_units(self) -> str:
        """Return the best available engineering-units string for this motor.

        Priority:
        1. ``DeviceInfo.metadata["units"]`` — static entry from the device
           catalog (happi, YAML, etc.).  Always tried first so the database
           can override whatever the hardware reports.
        2. ``motor.egu`` — the :class:`~ophyd.epics_motor.EpicsMotor`
           convenience property.  Calls ``motor_egu.get()``; because the
           component uses ``auto_monitor=True`` the value is cached after the
           first monitor update, so this is effectively free once connected.
        3. ``motor.user_readback.metadata["units"]`` / ``motor.readback.metadata["units"]``
           — the CA control-variable metadata dict on the readback signal,
           populated at connection time with zero additional CA calls.  Works
           for any :class:`~ophyd.epics_motor.EpicsMotor`-like device even
           when the ``egu`` property is absent.
        """
        # 1. Static catalog metadata
        if self._device_info.metadata:
            units = self._device_info.metadata.get("units", "") or ""
            if units:
                return units

        if self._motor is None:
            return ""

        # 2. EpicsMotor.egu convenience property
        if hasattr(self._motor, "egu"):
            try:
                units = self._motor.egu or ""
                if units:
                    return units
            except Exception:
                pass

        # 3. Readback-signal CA metadata dict (zero-cost after connection)
        for attr in ("user_readback", "readback"):
            sig = getattr(self._motor, attr, None)
            if sig is not None and hasattr(sig, "metadata"):
                try:
                    units = sig.metadata.get("units", "") or ""
                    if units:
                        return units
                except Exception:
                    pass

        return ""

    def _bind_signals(self) -> None:
        if self._motor is None:
            return
        if hasattr(self._motor, "user_readback"):
            self._rbv_display.signal = self._motor.user_readback
        elif hasattr(self._motor, "readback"):
            self._rbv_display.signal = self._motor.readback
        # OphydLineEdit stays bound regardless of mode — jog mode hides
        # this widget and shows a separate plain QLineEdit instead, so
        # the signal can keep updating silently in the background.
        self._setpoint_edit.signal = self._setpoint_signal()
        self._subscribe_moving()

    def _subscribe_moving(self) -> None:
        """Subscribe to the motor's moving signal so the status indicator
        flips to "warning" while in motion. EpicsMotor exposes this as
        ``motor_is_moving``; soft/simulated motors usually don't have it,
        so this is best-effort.
        """
        if self._motor is None:
            return
        sig = getattr(self._motor, "motor_is_moving", None)
        if sig is None or not hasattr(sig, "subscribe"):
            return
        try:
            self._moving_sub_id = sig.subscribe(self._on_moving_changed)
            self._moving_sub_signal = sig
        except Exception:
            self._moving_sub_id = None
            self._moving_sub_signal = None

    def _on_moving_changed(self, value: Any = None, **kwargs: Any) -> None:
        # ophyd callbacks fire on a background thread; bounce back to the
        # GUI thread before touching widgets.
        from lucid.utils.threads import invoke_in_main_thread

        invoke_in_main_thread(self._update_status_indicator)

    def _setpoint_signal(self) -> Any:
        """The ophyd signal that backs the setpoint entry in abs mode."""
        if self._motor is None:
            return None
        for attr in ("user_setpoint", "setpoint"):
            sig = getattr(self._motor, attr, None)
            if sig is not None:
                return sig
        return None

    def _unbind_signals(self) -> None:
        self._rbv_display.signal = None
        self._setpoint_edit.signal = None
        if self._moving_sub_signal is not None and self._moving_sub_id is not None:
            try:
                self._moving_sub_signal.unsubscribe(self._moving_sub_id)
            except Exception:
                pass
        self._moving_sub_signal = None
        self._moving_sub_id = None

    def _update_state(self) -> None:
        has_motor = self._motor is not None
        self._go_btn.setEnabled(has_motor)
        self._jog_left_btn.setEnabled(has_motor)
        self._stop_btn.setEnabled(has_motor)
        self._setpoint_edit.setEnabled(has_motor)
        self._jog_edit.setEnabled(has_motor)
        self._mode_btn.setEnabled(has_motor)
        if not has_motor:
            self._rbv_display._value_label.setText("...")
            self._name_label.setText(f"{self._device_info.name} (connecting...)")
        self._update_status_indicator()

    def _update_status_indicator(self) -> None:
        if self._motor is None:
            self._status_indicator.set_state("off")
            return
        connected = getattr(self._motor, "connected", True)
        if not connected:
            self._status_indicator.set_state("disconnected")
            return
        try:
            if bool(getattr(self._motor, "moving", False)):
                self._status_indicator.set_state("warning")
                return
        except Exception:
            pass
        self._status_indicator.set_state("on")

    def set_motor(self, ophyd_obj: Any) -> None:
        """Update the underlying motor object (e.g. after delayed connection)."""
        self._unbind_signals()
        self._motor = ophyd_obj
        self._bind_signals()
        self._name_label.setText(self._device_info.name)
        # Re-resolve units now that the motor is connected — EpicsMotor.egu
        # and signal.metadata["units"] are only populated after the CA
        # connection handshake completes, so the initial _setup_ui() call may
        # have returned "" for motors not yet in static.json metadata.
        self._units_label.setText(self._resolve_units())
        self._update_state()

    def _get_current_position(self) -> float | None:
        if self._motor is None:
            return None
        if hasattr(self._motor, "position"):
            return self._motor.position
        if hasattr(self._motor, "readback") and hasattr(self._motor.readback, "get"):
            return self._motor.readback.get()
        return None

    @Slot(bool)
    def _on_mode_toggled(self, checked: bool) -> None:
        self._is_jog_mode = checked
        self._setpoint_edit.setVisible(not checked)
        self._jog_edit.setVisible(checked)
        if checked:
            self._mode_btn.setText("Jog")
            self._jog_left_btn.setVisible(True)
            self._go_btn.setIcon(
                qta.icon("ph.arrow-fat-lines-right-fill", color="#90caf9")
            )
            self._go_btn.setToolTip("Jog positive by step")
        else:
            self._mode_btn.setText("Abs")
            self._jog_left_btn.setVisible(False)
            self._go_btn.setIcon(qta.icon("ph.arrow-fat-right-fill", color="#90caf9"))
            self._go_btn.setToolTip("Move to target")

    @Slot()
    def _on_go_clicked(self) -> None:
        # In jog mode the right-arrow button is "jog positive"; direction
        # comes from the button, not the sign of the entered value.
        self._move(direction=+1 if self._is_jog_mode else 0)

    @Slot()
    def _on_jog_left_clicked(self) -> None:
        self._move(direction=-1)

    def _move(self, direction: int) -> None:
        if self._motor is None:
            return
        entry = self._jog_edit if self._is_jog_mode else self._setpoint_edit
        text = entry.text().strip()
        if not text:
            return
        try:
            value = float(text)
            if self._is_jog_mode:
                current = self._get_current_position()
                if current is None:
                    self.control_error.emit("Cannot read current position for jog")
                    return
                target = current + direction * abs(value)
            else:
                target = value
            if hasattr(self._motor, "set"):
                self._motor.set(target)
                logger.info(
                    "CompactMotor: {} {} to {}",
                    "Jog" if self._is_jog_mode else "Move",
                    self._device_info.name,
                    target,
                )
        except ValueError:
            self.control_error.emit("Invalid value")
        except Exception as e:
            self.control_error.emit(f"Move failed: {e}")
            logger.error("CompactMotor move failed: {}", e)

    @Slot()
    def _on_stop_clicked(self) -> None:
        if self._motor is None:
            return
        try:
            if hasattr(self._motor, "stop"):
                self._motor.stop()
                logger.info("CompactMotor: stopped {}", self._device_info.name)
        except Exception as e:
            self.control_error.emit(f"Stop failed: {e}")
            logger.error("CompactMotor stop failed: {}", e)

    @Slot(QPoint)
    def _on_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        open_action = menu.addAction("Open Controller")
        open_action.triggered.connect(
            lambda: self.open_controller_requested.emit(self.device_id)
        )
        remove_action = menu.addAction("Remove from Favorites")
        remove_action.triggered.connect(
            lambda: self.remove_favorite_requested.emit(self.device_id)
        )
        menu.exec(self.mapToGlobal(pos))

    def closeEvent(self, event) -> None:
        self._unbind_signals()
        super().closeEvent(event)
