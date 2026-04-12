# PV Widget Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate all hand-rolled PV/signal widget code by migrating to `lucid.epics.widgets` (PVLineEdit, PVLabel, PVComboBox) for pure-EPICS widgets, and by creating a new parallel `OphydWidget` base class + widgets for ophyd-signal-based UI code.

**Architecture:** Two widget ecosystems exist today — EPICS PV widgets (caproto `PV` class) and ophyd signal widgets (ophyd `.subscribe()`/`.get()`/`.set()`). The `epics-pyside` library already provides reusable PV widgets but they're barely adopted. For ophyd, nothing reusable exists — 5 files reinvent the same boilerplate. This plan: (1) migrates the 2 pure-EPICS files to use existing PV widgets, (2) creates a minimal `OphydWidget` base + `OphydLineEdit`, `OphydComboBox`, `OphydLabel`, `OphydSpinBox` in `lucid.epics.widgets`, then (3) migrates the 5 ophyd files to use them.

**Tech Stack:** PySide6, caproto, ophyd, epics-pyside

---

## File Structure

### New files
- `src/lucid/epics/widgets/ophyd_base.py` — `OphydWidget` base class (parallel to `EpicsWidget`)
- `src/lucid/epics/widgets/ophyd_lineedit.py` — `OphydLineEdit`
- `src/lucid/epics/widgets/ophyd_label.py` — `OphydLabel`
- `src/lucid/epics/widgets/ophyd_combobox.py` — `OphydComboBox`
- `src/lucid/epics/widgets/ophyd_spinbox.py` — `OphydSpinBox`
- `tests/test_ophyd_widgets.py` — Tests for all new ophyd widgets

### Modified files
- `src/lucid/epics/widgets/__init__.py` — Export new ophyd widgets
- `src/lucid/epics/widgets/areadetector/controls.py` — Replace raw QLineEdit/QComboBox with PVLineEdit/PVComboBox
- `src/lucid/epics/widgets/motor.py` — Replace raw QLineEdit/QLabel with PVLineEdit/PVLabel
- `src/lucid/ui/widgets/signal_control.py` — Replace raw QLineEdit/QLabel with OphydLineEdit/OphydLabel
- `src/lucid/ui/widgets/motor_control.py` — Replace raw QLineEdit/QLabel with OphydLineEdit/OphydLabel
- `src/lucid/ui/widgets/camera/base.py` — Replace raw QLineEdit/QComboBox with OphydLineEdit/OphydComboBox
- `src/lucid/ui/widgets/camera/panels/cooler.py` — Replace raw QComboBox/QDoubleSpinBox/QLabel with OphydComboBox/OphydSpinBox/OphydLabel
- `src/lucid/ui/widgets/camera/panels/temperature.py` — Replace raw QDoubleSpinBox/QLabel with OphydSpinBox/OphydLabel

---

## Phase 1: Migrate pure-EPICS widgets (use existing PV widgets)

### Task 1: Migrate `PVAreaDetectorControls` to use PV widgets

The simplest migration — `areadetector/controls.py` has 3 raw QLineEdits and 1 raw QComboBox that each map 1:1 to existing PV widgets. The widget manages its own PV connections with a `_pvs` dict and manual `_on_pv_value` dispatch. After migration, the PV widgets handle their own connections, formatting, hasFocus() protection, and write-on-enter — eliminating ~100 lines of boilerplate.

**Files:**
- Modify: `src/lucid/epics/widgets/areadetector/controls.py`

**Key insight:** AreaDetector uses separate setpoint and RBV PVs (e.g., `AcquireTime` for write, `AcquireTime_RBV` for readback). `PVLineEdit` binds to one PV. The cleanest approach: bind PVLineEdit to the RBV for display, and override write to put to the setpoint PV. However, this complicates the widget. Simpler: bind PVLineEdit to the setpoint PV directly. The readback will differ slightly from setpoint during transitions, but this is standard motor-record behavior and acceptable for camera controls.

Actually, even simpler: keep the current `_pvs` dict for the PVs that don't map to widgets (acquire, detector_state), and use `PVLineEdit`/`PVComboBox` for the 4 input fields. The PV widgets auto-connect and auto-format. We remove the `_update_*_display` methods and `_on_*_changed` handlers for those 4 fields.

- [ ] **Step 1: Read the current file and identify all raw widget instances**

The 4 raw widgets to replace (all in `_setup_ui`):
- Line 197: `self._acquire_time_edit = QLineEdit()` → `PVLineEdit` bound to `{cam_prefix}AcquireTime`
- Line 207: `self._acquire_period_edit = QLineEdit()` → `PVLineEdit` bound to `{cam_prefix}AcquirePeriod`
- Line 217: `self._num_images_edit = QLineEdit()` → `PVLineEdit` bound to `{cam_prefix}NumImages`
- Line 226: `self._image_mode_combo = QComboBox()` → `PVComboBox` bound to `{cam_prefix}ImageMode`

- [ ] **Step 2: Replace imports and raw widgets in `_setup_ui`**

Replace QLineEdit/QComboBox with PVLineEdit/PVComboBox. The PV name can't be set at construction time because `_cam_prefix` may change — so construct with empty pv_name and set it in `_connect_pvs`.

Add imports:
```python
from lucid.epics.widgets.lineedit import PVLineEdit
from lucid.epics.widgets.combobox import PVComboBox
```

Replace in `_setup_ui`:
```python
# Was: self._acquire_time_edit = QLineEdit()
#      self._acquire_time_edit.setValidator(QDoubleValidator(0, 10000, 6))
#      self._acquire_time_edit.setPlaceholderText("seconds")
#      self._acquire_time_edit.editingFinished.connect(self._on_acquire_time_changed)
self._acquire_time_edit = PVLineEdit(show_units=False, write_on_enter=True)

# Was: self._acquire_period_edit = QLineEdit()
#      ... (same pattern)
self._acquire_period_edit = PVLineEdit(show_units=False, write_on_enter=True)

# Was: self._num_images_edit = QLineEdit()
#      ... (same pattern)
self._num_images_edit = PVLineEdit(show_units=False, write_on_enter=True, precision=0)

# Was: self._image_mode_combo = QComboBox()
#      self._image_mode_combo.addItems(IMAGE_MODES)
#      self._image_mode_combo.currentIndexChanged.connect(self._on_image_mode_changed)
self._image_mode_combo = PVComboBox(write_on_change=True)
self._image_mode_combo.set_items(IMAGE_MODES)
```

- [ ] **Step 3: Update `_connect_pvs` to set PV names on widgets and remove handled PVs**

The 4 widget fields no longer need manual PV objects. Remove `AcquireTime`, `AcquireTime_RBV`, `AcquirePeriod`, `AcquirePeriod_RBV`, `NumImages`, `NumImages_RBV`, `ImageMode`, `ImageMode_RBV` from `pv_fields`. Instead, set the pv_name property on each widget:

```python
def _connect_pvs(self) -> None:
    if not self._cam_prefix:
        return

    # PV widgets manage their own connections
    self._acquire_time_edit.pv_name = f"{self._cam_prefix}AcquireTime"
    self._acquire_period_edit.pv_name = f"{self._cam_prefix}AcquirePeriod"
    self._num_images_edit.pv_name = f"{self._cam_prefix}NumImages"
    self._image_mode_combo.pv_name = f"{self._cam_prefix}ImageMode"

    from lucid.epics.ca.pv import PV

    # Only manual PVs for non-widget fields
    pv_fields = {
        "Acquire": "acquire",
        "DetectorState_RBV": "detector_state",
    }

    for field, name in pv_fields.items():
        pv_name = f"{self._cam_prefix}{field}"
        pv = PV(pv_name, parent=self)
        pv.value_changed.connect(lambda v, n=name: self._on_pv_value(n, v))
        pv.connection_changed.connect(lambda c, n=name: self._on_pv_connection(n, c))
        pv.connect_pv()
        self._pvs[name] = pv
```

- [ ] **Step 4: Remove dead methods**

Delete these methods that are now handled by PV widgets:
- `_update_acquire_time_display`
- `_update_acquire_period_display`
- `_update_num_images_display`
- `_update_image_mode_display`
- `_on_acquire_time_changed`
- `_on_acquire_period_changed`
- `_on_num_images_changed`
- `_on_image_mode_changed`

Simplify `_on_pv_value` to only handle `acquire` and `detector_state`.

Remove the now-unused `QLineEdit`, `QComboBox`, `QDoubleValidator`, `QIntValidator` imports.

- [ ] **Step 5: Update `_set_controls_enabled` and `_disconnect_pvs`**

`_set_controls_enabled` — PV widgets respect `readonly` property. Set readonly instead of setEnabled:
```python
def _set_controls_enabled(self, enabled: bool) -> None:
    self._acquire_time_edit.readonly = not enabled
    self._acquire_period_edit.readonly = not enabled
    self._num_images_edit.readonly = not enabled
    self._image_mode_combo.readonly = not enabled
    self._acquire_btn.setEnabled(enabled)
    self._abort_btn.setEnabled(enabled)
```

`_disconnect_pvs` — also clear widget PV names:
```python
def _disconnect_pvs(self) -> None:
    for pv in self._pvs.values():
        pv.disconnect_pv()
        pv.deleteLater()
    self._pvs.clear()
    self._connected_pvs.clear()
    self._values.clear()
    self._conn_indicator.set_state("disconnected")
    # Disconnect PV widgets
    self._acquire_time_edit.pv_name = ""
    self._acquire_period_edit.pv_name = ""
    self._num_images_edit.pv_name = ""
    self._image_mode_combo.pv_name = ""
```

- [ ] **Step 6: Update public API properties to read from PV widgets**

The `acquire_time`, `acquire_period`, `num_images`, `image_mode` properties currently read from `self._values`. Update them to read from the PV widget's `_value`:
```python
@property
def acquire_time(self) -> float | None:
    val = self._acquire_time_edit._value
    return float(val) if val is not None else None

@property
def acquire_period(self) -> float | None:
    val = self._acquire_period_edit._value
    return float(val) if val is not None else None

@property
def num_images(self) -> int | None:
    val = self._num_images_edit._value
    return int(val) if val is not None else None

@property
def image_mode(self) -> str | None:
    val = self._image_mode_combo._value
    if val is not None:
        idx = int(val)
        if 0 <= idx < len(IMAGE_MODES):
            return IMAGE_MODES[idx]
    return None
```

Also update `set_acquire_time`, `set_acquire_period`, `set_num_images`, `set_image_mode` to write through the widgets:
```python
def set_acquire_time(self, seconds: float) -> None:
    self._acquire_time_edit.write_value(seconds)

def set_acquire_period(self, seconds: float) -> None:
    self._acquire_period_edit.write_value(seconds)

def set_num_images(self, count: int) -> None:
    self._num_images_edit.write_value(count)

def set_image_mode(self, mode: str) -> None:
    if mode in IMAGE_MODES:
        idx = IMAGE_MODES.index(mode)
        self._image_mode_combo.write_value(idx)
```

- [ ] **Step 7: Commit**

```bash
git add src/lucid/epics/widgets/areadetector/controls.py
git commit -m "refactor(areadetector): replace raw QLineEdit/QComboBox with PVLineEdit/PVComboBox"
```

---

### Task 2: Migrate `PVMotor` to use PV widgets

`PVMotor` in `motor.py` is more complex — it manages ~17 PVs and has custom logic for motor status, limits, MSTA decoding, etc. The migration targets are the 4 QLineEdits and 2 QLabels that display/edit PV values. The status indicators, buttons, and MSTA decoder stay as-is.

**Files:**
- Modify: `src/lucid/epics/widgets/motor.py`

**Approach:** Replace the 4 QLineEdits (setpoint, tweak, velocity, acceleration) with `PVLineEdit`, and the RBV QLabel + units QLabel with `PVLabel`. Keep the rest of the manual PV infrastructure for status/limits/MSTA.

- [ ] **Step 1: Add imports**

```python
from lucid.epics.widgets.lineedit import PVLineEdit
from lucid.epics.widgets.label import PVLabel
```

- [ ] **Step 2: Replace RBV display with PVLabel**

In `_setup_ui`, replace:
```python
# Was:
# self._rbv_display = QLabel("---")
# self._rbv_display.setStyleSheet("""...""")
# self._units_label = QLabel("")
self._rbv_display = PVLabel(show_units=True)
self._rbv_display.setStyleSheet("""
    font-size: 18pt;
    font-weight: bold;
    font-family: monospace;
    padding: 4px 8px;
""")
```

Remove the separate `self._units_label` — PVLabel handles units internally.

- [ ] **Step 3: Replace setpoint QLineEdit with PVLineEdit**

```python
# Was:
# self._setpoint_edit = QLineEdit()
# self._setpoint_edit.setPlaceholderText("Enter position")
# self._setpoint_edit.setValidator(QDoubleValidator())
# self._setpoint_edit.returnPressed.connect(self._on_setpoint_enter)
self._setpoint_edit = PVLineEdit(show_units=False, write_on_enter=True, write_on_focus_out=False)
```

The Go button still works: `_on_go_clicked` reads `self._setpoint_edit.text()` and does `self._pvs["setpoint"].put(value)`. But now we can simplify — PVLineEdit's write_on_enter handles the PV put. Keep the Go button as explicit "move now" for users who click instead of pressing Enter.

- [ ] **Step 4: Replace tweak, velocity, acceleration QLineEdits with PVLineEdit**

```python
# Tweak step size
self._tweak_edit = PVLineEdit(show_units=False, write_on_enter=True, write_on_focus_out=True)

# Velocity (in advanced section)
self._velo_edit = PVLineEdit(show_units=False, write_on_enter=True, write_on_focus_out=True)

# Acceleration (in advanced section)  
self._accl_edit = PVLineEdit(show_units=False, write_on_enter=True, write_on_focus_out=True)
```

- [ ] **Step 5: Update `_connect_pvs` to set PV names on widgets**

After the PV loop, add:
```python
# Set PV names on widget fields
self._rbv_display.pv_name = f"{self._prefix}.RBV"
self._setpoint_edit.pv_name = f"{self._prefix}.VAL"
self._tweak_edit.pv_name = f"{self._prefix}.TWV"
self._velo_edit.pv_name = f"{self._prefix}.VELO"
self._accl_edit.pv_name = f"{self._prefix}.ACCL"
```

Remove `VAL`, `RBV`, `TWV`, `VELO`, `ACCL`, `EGU`, `PREC` from the `pv_fields` dict — these are now managed by the PV widgets. Keep `TWF`, `TWR`, `MOVN`, `DMOV`, `HLS`, `LLS`, `TDIR`, `STOP`, `SPMG`, `HLM`, `LLM`, `MSTA` as manual PVs.

- [ ] **Step 6: Remove dead display/handler methods**

Delete:
- `_update_readback` — PVLabel auto-updates
- `_update_setpoint_display` — PVLineEdit auto-updates
- `_update_tweak_display` — PVLineEdit auto-updates
- `_update_velocity_display` — PVLineEdit auto-updates
- `_update_accel_display` — PVLineEdit auto-updates
- `_update_units` — PVLabel auto-reads units from metadata
- `_on_velo_changed` — PVLineEdit writes on enter
- `_on_accl_changed` — PVLineEdit writes on enter
- `_on_tweak_value_changed` — PVLineEdit writes on enter

Simplify `_on_pv_value` to remove cases for `readback`, `setpoint`, `tweak_val`, `velocity`, `acceleration`, `units`, `precision`.

- [ ] **Step 7: Update `_on_go_clicked` and tweak methods**

`_on_go_clicked` needs to read from PVLineEdit and write to the VAL PV. Since PVLineEdit is now bound to VAL, pressing Enter already writes. The Go button just needs to trigger the same write:

```python
def _on_go_clicked(self) -> None:
    try:
        value = float(self._setpoint_edit._line_edit.text())
        self._setpoint_edit.write_value(value)
    except ValueError:
        pass
```

For `_do_relative_move`, read tweak value from the widget and readback from PVLabel:
```python
def _do_relative_move(self, direction: int) -> None:
    try:
        tweak_val = float(self._tweak_edit._line_edit.text())
    except ValueError:
        tweak_val = 1.0
    current = self._rbv_display._value
    if current is None:
        current = 0.0
    new_pos = current + (direction * tweak_val)
    self._setpoint_edit.write_value(new_pos)
```

- [ ] **Step 8: Update `_disconnect_pvs` and `_set_controls_enabled`**

```python
def _disconnect_pvs(self) -> None:
    for pv in self._pvs.values():
        pv.disconnect_pv()
        pv.deleteLater()
    self._pvs.clear()
    self._connected_pvs.clear()
    self._values.clear()
    # Disconnect PV widgets
    self._rbv_display.pv_name = ""
    self._setpoint_edit.pv_name = ""
    self._tweak_edit.pv_name = ""
    self._velo_edit.pv_name = ""
    self._accl_edit.pv_name = ""
    self._update_connection_display(False)
```

Update `_set_controls_enabled` to use `readonly` for PV widgets, keep `setEnabled` for buttons.

- [ ] **Step 9: Update public API properties**

Update `position` and `setpoint` properties:
```python
@property
def position(self) -> float | None:
    return self._rbv_display._value

@property
def setpoint(self) -> float | None:
    return self._setpoint_edit._value
```

- [ ] **Step 10: Remove unused imports**

Remove `QLineEdit`, `QDoubleValidator` from imports. Keep `QLabel` for status labels that are NOT PV-bound.

- [ ] **Step 11: Commit**

```bash
git add src/lucid/epics/widgets/motor.py
git commit -m "refactor(motor): replace raw QLineEdit/QLabel with PVLineEdit/PVLabel"
```

---

## Phase 2: Create ophyd widget library

### Task 3: Create `OphydWidget` base class

The ophyd analog of `EpicsWidget`. Key differences from `EpicsWidget`:
- Connects to ophyd signals (`.subscribe()`, `.get()`, `.set()`) instead of caproto PVs
- Thread-safe value delivery via `invoke_in_main_thread` (ophyd callbacks come from background threads)
- Handles both sync and async signals (BCSSignal etc.)
- Polling fallback when subscription isn't available

**Files:**
- Create: `src/lucid/epics/widgets/ophyd_base.py`
- Test: `tests/test_ophyd_widgets.py`

- [ ] **Step 1: Write the failing test for OphydWidget**

```python
# tests/test_ophyd_widgets.py
"""Tests for ophyd-based reusable widgets."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from lucid.epics.widgets.ophyd_base import OphydWidget


class ConcreteOphydWidget(OphydWidget):
    """Concrete subclass for testing abstract base."""

    def _update_display(self) -> None:
        pass

    def _get_widget_value(self):
        return self._value

    def _set_widget_value(self, value) -> None:
        self._value = value


@pytest.fixture
def widget(qtbot):
    w = ConcreteOphydWidget()
    qtbot.addWidget(w)
    return w


class TestOphydWidgetBase:
    def test_initial_state(self, widget):
        assert widget.signal is None
        assert widget._value is None
        assert widget._connected is False
        assert widget.readonly is False

    def test_set_signal_stores_reference(self, widget):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 42.0
        widget.signal = sig
        assert widget.signal is sig

    def test_readonly_prevents_write(self, widget):
        widget.readonly = True
        with pytest.raises(RuntimeError, match="readonly"):
            widget.write_value(42)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/test_ophyd_widgets.py -v`
Expected: FAIL with import error (module doesn't exist yet)

- [ ] **Step 3: Implement OphydWidget**

```python
# src/lucid/epics/widgets/ophyd_base.py
"""Base class for ophyd signal widgets.

Parallel to EpicsWidget (which wraps caproto PVs), this base class
wraps ophyd signals — providing subscription, thread-safe UI updates,
and a uniform interface for widgets that control ophyd devices.
"""
from __future__ import annotations

import inspect
from abc import abstractmethod
from typing import Any, ClassVar

from PySide6.QtCore import Property, Signal, Slot, QTimer
from PySide6.QtWidgets import QWidget

from lucid.epics.widgets.style import WidgetStyles


class OphydWidget(QWidget):
    """Abstract base class for widgets bound to ophyd signals.

    Subclasses must implement:
    - _update_display(): Update the widget from the current value
    - _get_widget_value(): Get the current value from the widget UI
    - _set_widget_value(): Set the widget to display a specific value

    The widget subscribes to the ophyd signal for value updates and
    marshals callbacks to the Qt main thread.
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
    ) -> None:
        super().__init__(parent)
        self._signal: Any = None
        self._sub_id: int | None = None
        self._readonly = readonly
        self._value: Any = None
        self._connected = False
        self._poll_timer: QTimer | None = None

        if signal is not None:
            self.signal = signal

    @Property(bool)
    def readonly(self) -> bool:
        return self._readonly

    @readonly.setter
    def readonly(self, value: bool) -> None:
        self._readonly = value
        self._update_readonly_state()

    @property
    def signal(self) -> Any:
        return self._signal

    @signal.setter
    def signal(self, sig: Any) -> None:
        self._disconnect_signal()
        self._signal = sig
        if sig is not None:
            self._connect_signal()

    def _connect_signal(self) -> None:
        """Subscribe to the ophyd signal for value updates."""
        if self._signal is None:
            return

        # Check connection state
        if hasattr(self._signal, "connected"):
            self._connected = bool(self._signal.connected)
        else:
            self._connected = True
        self._update_connection_style()
        self.connection_changed.emit(self._connected)

        # Try to subscribe
        try:
            self._sub_id = self._signal.subscribe(self._on_signal_value)
        except Exception:
            # Fallback to polling if subscribe isn't available
            self._start_polling()

        # Read initial value
        self._read_initial_value()

    def _disconnect_signal(self) -> None:
        """Unsubscribe from the current signal."""
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

    def _on_signal_value(self, value: Any = None, **kwargs) -> None:
        """Ophyd subscription callback — may run on background thread."""
        from lucid.utils.threads import invoke_in_main_thread

        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]

        self._value = value
        invoke_in_main_thread(self._apply_value_update)

    @Slot()
    def _apply_value_update(self) -> None:
        """Apply value update on main thread."""
        self._update_display()
        self.value_changed.emit(self._value)

    def _read_initial_value(self) -> None:
        """Read the initial value from the signal."""
        if self._signal is None:
            return
        try:
            val = self._signal.get()
            if inspect.isawaitable(val):
                return  # Don't block — subscription will deliver
            if hasattr(val, "__len__") and not isinstance(val, (str, bytes)):
                if len(val) == 1:
                    val = val[0]
            self._value = val
            self._update_display()
            self.value_changed.emit(self._value)
        except Exception:
            pass

    def _start_polling(self) -> None:
        """Start polling fallback (500ms) for signals that don't support subscribe."""
        if self._poll_timer is None:
            self._poll_timer = QTimer(self)
            self._poll_timer.timeout.connect(self._poll_value)
        self._poll_timer.start(500)

    def _stop_polling(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()

    @Slot()
    def _poll_value(self) -> None:
        """Poll the signal for its current value."""
        self._read_initial_value()

    def write_value(self, value: Any | None = None) -> None:
        """Write a value to the ophyd signal."""
        if self._readonly:
            raise RuntimeError("Widget is readonly")
        if self._signal is None or not self._connected:
            raise RuntimeError("Signal is not connected")

        if value is None:
            value = self._get_widget_value()

        if hasattr(self._signal, "put"):
            result = self._signal.put(value)
            if inspect.isawaitable(result):
                import asyncio, threading
                def _run():
                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(result)
                    finally:
                        loop.close()
                threading.Thread(target=_run, daemon=True).start()
        elif hasattr(self._signal, "set"):
            self._signal.set(value)

    def _update_connection_style(self) -> None:
        if self._connected:
            self.setStyleSheet(WidgetStyles.connected())
        else:
            self.setStyleSheet(WidgetStyles.disconnected())

    def _update_readonly_state(self) -> None:
        """Override in subclasses to disable editing when readonly."""
        pass

    @abstractmethod
    def _update_display(self) -> None:
        pass

    @abstractmethod
    def _get_widget_value(self) -> Any:
        pass

    @abstractmethod
    def _set_widget_value(self, value: Any) -> None:
        pass

    def closeEvent(self, event) -> None:
        self._disconnect_signal()
        super().closeEvent(event)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/test_ophyd_widgets.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/lucid/epics/widgets/ophyd_base.py tests/test_ophyd_widgets.py
git commit -m "feat: add OphydWidget base class for ophyd signal widgets"
```

---

### Task 4: Create `OphydLineEdit`

**Files:**
- Create: `src/lucid/epics/widgets/ophyd_lineedit.py`
- Test: `tests/test_ophyd_widgets.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ophyd_widgets.py`:
```python
from lucid.epics.widgets.ophyd_lineedit import OphydLineEdit


class TestOphydLineEdit:
    def test_displays_value(self, qtbot):
        w = OphydLineEdit()
        qtbot.addWidget(w)
        w._value = 3.14159
        w._update_display()
        assert w._line_edit.text() == "3.1416"  # default precision=4

    def test_write_on_enter(self, qtbot):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0.0

        w = OphydLineEdit(write_on_enter=True)
        qtbot.addWidget(w)
        w.signal = sig
        w._connected = True
        w._line_edit.setText("42.0")
        w._on_return_pressed()
        sig.put.assert_called_once_with(42.0)

    def test_modified_style(self, qtbot):
        w = OphydLineEdit()
        qtbot.addWidget(w)
        w._value = 1.0
        w._update_display()
        w._line_edit.setText("2.0")  # User edits
        assert w._modified is True

    def test_readonly_disables_editing(self, qtbot):
        w = OphydLineEdit(readonly=True)
        qtbot.addWidget(w)
        assert w._line_edit.isReadOnly()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/test_ophyd_widgets.py::TestOphydLineEdit -v`
Expected: FAIL

- [ ] **Step 3: Implement OphydLineEdit**

```python
# src/lucid/epics/widgets/ophyd_lineedit.py
"""OphydLineEdit — text input for ophyd signal values."""
from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Property, Signal, Slot
from PySide6.QtWidgets import QLineEdit, QWidget, QHBoxLayout

from lucid.epics.widgets.ophyd_base import OphydWidget
from lucid.epics.widgets.style import WidgetStyles


class OphydLineEdit(OphydWidget):
    """Line edit widget for editing ophyd signal values.

    Features:
    - Text input for numeric and string signals
    - Enter key or focus-out to write value
    - Visual feedback for modified but not-yet-written values
    - Type coercion based on current signal value type
    """

    widget_type: ClassVar[str] = "OphydLineEdit"
    widget_description: ClassVar[str] = "Text input for ophyd signal values"

    editing_finished = Signal()
    value_written = Signal(object)

    def __init__(
        self,
        signal: Any = None,
        parent: QWidget | None = None,
        precision: int = 4,
        write_on_enter: bool = True,
        write_on_focus_out: bool = False,
        readonly: bool = False,
    ) -> None:
        self._precision = precision
        self._write_on_enter = write_on_enter
        self._write_on_focus_out = write_on_focus_out
        self._modified = False

        super().__init__(signal, parent, readonly=readonly)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._line_edit = QLineEdit()
        self._layout.addWidget(self._line_edit)

        self._line_edit.textChanged.connect(self._on_text_changed)
        self._line_edit.returnPressed.connect(self._on_return_pressed)
        self._line_edit.editingFinished.connect(self._on_editing_finished)

        self._update_readonly_state()

    @Property(int)
    def precision(self) -> int:
        return self._precision

    @precision.setter
    def precision(self, value: int) -> None:
        self._precision = value
        self._update_display()

    def _update_display(self) -> None:
        if self._modified:
            return
        if self._value is None:
            self._line_edit.setText("")
            return
        text = self._format_value(self._value)
        self._line_edit.blockSignals(True)
        self._line_edit.setText(text)
        self._line_edit.blockSignals(False)

    def _get_widget_value(self) -> Any:
        text = self._line_edit.text().strip()
        if self._value is not None:
            try:
                if isinstance(self._value, int):
                    return int(text)
                elif isinstance(self._value, float):
                    return float(text)
            except ValueError:
                pass
        return text

    def _set_widget_value(self, value: Any) -> None:
        self._value = value
        self._modified = False
        self._update_display()

    def _format_value(self, value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.{self._precision}f}"
        return str(value)

    def _update_readonly_state(self) -> None:
        self._line_edit.setReadOnly(self._readonly or not self._connected)

    @Slot()
    def _on_text_changed(self) -> None:
        self._modified = True
        self._update_modified_style()

    @Slot()
    def _on_return_pressed(self) -> None:
        if self._write_on_enter and self._modified:
            self._write_current_value()

    @Slot()
    def _on_editing_finished(self) -> None:
        self.editing_finished.emit()
        if self._write_on_focus_out and self._modified:
            self._write_current_value()

    def _write_current_value(self) -> None:
        if not self._connected or self._readonly:
            return
        try:
            value = self._get_widget_value()
            self.write_value(value)
            self._modified = False
            self._update_modified_style()
            self.value_written.emit(value)
        except Exception:
            self._modified = False
            self._update_display()

    def _update_modified_style(self) -> None:
        if self._modified:
            self._line_edit.setStyleSheet(WidgetStyles.modified())
        else:
            self._line_edit.setStyleSheet("")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/test_ophyd_widgets.py::TestOphydLineEdit -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/lucid/epics/widgets/ophyd_lineedit.py tests/test_ophyd_widgets.py
git commit -m "feat: add OphydLineEdit for ophyd signal text input"
```

---

### Task 5: Create `OphydLabel`

**Files:**
- Create: `src/lucid/epics/widgets/ophyd_label.py`
- Test: `tests/test_ophyd_widgets.py` (append)

- [ ] **Step 1: Write the failing test**

```python
from lucid.epics.widgets.ophyd_label import OphydLabel


class TestOphydLabel:
    def test_displays_value(self, qtbot):
        w = OphydLabel()
        qtbot.addWidget(w)
        w._value = 25.678
        w._update_display()
        assert w._value_label.text() == "25.6780"

    def test_displays_none_as_dashes(self, qtbot):
        w = OphydLabel()
        qtbot.addWidget(w)
        w._value = None
        w._update_display()
        assert w._value_label.text() == "---"

    def test_custom_precision(self, qtbot):
        w = OphydLabel(precision=2)
        qtbot.addWidget(w)
        w._value = 3.14159
        w._update_display()
        assert w._value_label.text() == "3.14"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/rp/PycharmProjects/ncs/ncs && .venv/Scripts/python -m pytest tests/test_ophyd_widgets.py::TestOphydLabel -v`

- [ ] **Step 3: Implement OphydLabel**

```python
# src/lucid/epics/widgets/ophyd_label.py
"""OphydLabel — read-only display for ophyd signal values."""
from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtWidgets import QLabel, QWidget, QHBoxLayout

from lucid.epics.widgets.ophyd_base import OphydWidget


class OphydLabel(OphydWidget):
    """Read-only label that displays an ophyd signal value.

    Features:
    - Automatic value updates from signal subscription
    - Configurable precision for floats
    - Connection state indication via styling
    """

    widget_type: ClassVar[str] = "OphydLabel"
    widget_description: ClassVar[str] = "Read-only label for ophyd signal values"

    def __init__(
        self,
        signal: Any = None,
        parent: QWidget | None = None,
        precision: int = 4,
    ) -> None:
        self._precision = precision
        super().__init__(signal, parent, readonly=True)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._value_label = QLabel("---")
        self._layout.addWidget(self._value_label)

    def _update_display(self) -> None:
        if self._value is None:
            self._value_label.setText("---")
            return
        self._value_label.setText(self._format_value(self._value))

    def _format_value(self, value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.{self._precision}f}"
        return str(value)

    def _get_widget_value(self) -> Any:
        return self._value

    def _set_widget_value(self, value: Any) -> None:
        self._value = value
        self._update_display()
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add src/lucid/epics/widgets/ophyd_label.py tests/test_ophyd_widgets.py
git commit -m "feat: add OphydLabel for read-only ophyd signal display"
```

---

### Task 6: Create `OphydComboBox`

**Files:**
- Create: `src/lucid/epics/widgets/ophyd_combobox.py`
- Test: `tests/test_ophyd_widgets.py` (append)

- [ ] **Step 1: Write the failing test**

```python
from lucid.epics.widgets.ophyd_combobox import OphydComboBox


class TestOphydComboBox:
    def test_set_items(self, qtbot):
        w = OphydComboBox()
        qtbot.addWidget(w)
        w.set_items(["Single", "Multiple", "Continuous"])
        assert w._combo.count() == 3
        assert w._combo.itemText(1) == "Multiple"

    def test_displays_value_as_index(self, qtbot):
        w = OphydComboBox()
        qtbot.addWidget(w)
        w.set_items(["Off", "On"])
        w._value = 1
        w._update_display()
        assert w._combo.currentIndex() == 1

    def test_write_on_change(self, qtbot):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0

        w = OphydComboBox(write_on_change=True)
        qtbot.addWidget(w)
        w.set_items(["Off", "On"])
        w.signal = sig
        w._connected = True
        w._combo.setCurrentIndex(1)
        sig.put.assert_called_with(1)
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement OphydComboBox**

```python
# src/lucid/epics/widgets/ophyd_combobox.py
"""OphydComboBox — dropdown for ophyd enum signal values."""
from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QComboBox, QWidget, QHBoxLayout

from lucid.epics.widgets.ophyd_base import OphydWidget


class OphydComboBox(OphydWidget):
    """Combo box for selecting ophyd enum signal values.

    Features:
    - Manual item population (ophyd doesn't auto-provide enum strings)
    - Selection changes can auto-write to signal
    - Shows current signal value as selected item
    """

    widget_type: ClassVar[str] = "OphydComboBox"
    widget_description: ClassVar[str] = "Dropdown for ophyd enum signal values"

    selection_changed = Signal(int, str)
    value_written = Signal(object)

    def __init__(
        self,
        signal: Any = None,
        parent: QWidget | None = None,
        write_on_change: bool = True,
    ) -> None:
        self._write_on_change = write_on_change
        self._updating_from_signal = False

        super().__init__(signal, parent, readonly=False)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._combo = QComboBox()
        self._layout.addWidget(self._combo)

        self._combo.currentIndexChanged.connect(self._on_index_changed)

    def set_items(self, items: list[str]) -> None:
        self._combo.clear()
        for i, text in enumerate(items):
            self._combo.addItem(text, i)

    def _update_display(self) -> None:
        if self._value is None:
            return
        self._updating_from_signal = True
        try:
            index = int(self._value)
            if 0 <= index < self._combo.count():
                self._combo.setCurrentIndex(index)
        except (ValueError, TypeError):
            text = str(self._value)
            index = self._combo.findText(text)
            if index >= 0:
                self._combo.setCurrentIndex(index)
        finally:
            self._updating_from_signal = False

    def _get_widget_value(self) -> Any:
        return self._combo.currentIndex()

    def _set_widget_value(self, value: Any) -> None:
        self._value = value
        self._update_display()

    def _update_readonly_state(self) -> None:
        self._combo.setEnabled(not self._readonly and self._connected)

    @Slot(int)
    def _on_index_changed(self, index: int) -> None:
        if self._updating_from_signal:
            return
        text = self._combo.currentText()
        self.selection_changed.emit(index, text)
        if self._write_on_change and self._connected and not self._readonly:
            try:
                self.write_value(index)
                self.value_written.emit(index)
            except Exception:
                self._update_display()
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add src/lucid/epics/widgets/ophyd_combobox.py tests/test_ophyd_widgets.py
git commit -m "feat: add OphydComboBox for ophyd enum signal selection"
```

---

### Task 7: Create `OphydSpinBox`

Needed by cooler.py and temperature.py for temperature setpoints.

**Files:**
- Create: `src/lucid/epics/widgets/ophyd_spinbox.py`
- Test: `tests/test_ophyd_widgets.py` (append)

- [ ] **Step 1: Write the failing test**

```python
from lucid.epics.widgets.ophyd_spinbox import OphydSpinBox


class TestOphydSpinBox:
    def test_displays_value(self, qtbot):
        w = OphydSpinBox(minimum=-100.0, maximum=50.0)
        qtbot.addWidget(w)
        w._value = -20.5
        w._update_display()
        assert w._spinbox.value() == -20.5

    def test_write_on_change(self, qtbot):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = -20.0

        w = OphydSpinBox(write_on_change=True)
        qtbot.addWidget(w)
        w.signal = sig
        w._connected = True
        w._spinbox.setValue(-15.0)
        sig.put.assert_called_with(-15.0)
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement OphydSpinBox**

```python
# src/lucid/epics/widgets/ophyd_spinbox.py
"""OphydSpinBox — numeric spin box for ophyd signal values."""
from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QDoubleSpinBox, QWidget, QHBoxLayout

from lucid.epics.widgets.ophyd_base import OphydWidget


class OphydSpinBox(OphydWidget):
    """Spin box for numeric ophyd signal values.

    Features:
    - Range limits (min/max)
    - Configurable precision (decimals)
    - Auto-write on value change or explicit write
    """

    widget_type: ClassVar[str] = "OphydSpinBox"
    widget_description: ClassVar[str] = "Spin box for numeric ophyd signal values"

    value_written = Signal(object)

    def __init__(
        self,
        signal: Any = None,
        parent: QWidget | None = None,
        minimum: float = -1e6,
        maximum: float = 1e6,
        decimals: int = 1,
        write_on_change: bool = True,
    ) -> None:
        self._write_on_change = write_on_change
        self._updating_from_signal = False

        super().__init__(signal, parent, readonly=False)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._spinbox = QDoubleSpinBox()
        self._spinbox.setMinimum(minimum)
        self._spinbox.setMaximum(maximum)
        self._spinbox.setDecimals(decimals)
        self._layout.addWidget(self._spinbox)

        self._spinbox.valueChanged.connect(self._on_value_changed)

    def _update_display(self) -> None:
        if self._value is None:
            return
        self._updating_from_signal = True
        try:
            self._spinbox.setValue(float(self._value))
        except (ValueError, TypeError):
            pass
        finally:
            self._updating_from_signal = False

    def _get_widget_value(self) -> float:
        return self._spinbox.value()

    def _set_widget_value(self, value: Any) -> None:
        self._value = value
        self._update_display()

    def _update_readonly_state(self) -> None:
        self._spinbox.setReadOnly(self._readonly or not self._connected)

    @Slot(float)
    def _on_value_changed(self, value: float) -> None:
        if self._updating_from_signal:
            return
        if self._write_on_change and self._connected and not self._readonly:
            try:
                self.write_value(value)
                self.value_written.emit(value)
            except Exception:
                self._update_display()
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add src/lucid/epics/widgets/ophyd_spinbox.py tests/test_ophyd_widgets.py
git commit -m "feat: add OphydSpinBox for numeric ophyd signal editing"
```

---

### Task 8: Export new ophyd widgets from `__init__.py`

**Files:**
- Modify: `src/lucid/epics/widgets/__init__.py`

- [ ] **Step 1: Update exports**

Add to `src/lucid/epics/widgets/__init__.py`:
```python
from lucid.epics.widgets.ophyd_base import OphydWidget
from lucid.epics.widgets.ophyd_lineedit import OphydLineEdit
from lucid.epics.widgets.ophyd_label import OphydLabel
from lucid.epics.widgets.ophyd_combobox import OphydComboBox
from lucid.epics.widgets.ophyd_spinbox import OphydSpinBox
```

Add to `__all__`:
```python
"OphydWidget",
"OphydLineEdit",
"OphydLabel",
"OphydComboBox",
"OphydSpinBox",
```

- [ ] **Step 2: Commit**

```bash
git add src/lucid/epics/widgets/__init__.py
git commit -m "feat: export ophyd widgets from lucid.epics.widgets"
```

---

## Phase 3: Migrate ophyd-based UI code

### Task 9: Migrate `signal_control.py`

Replace the raw QLineEdit and QLabel in `SignalControlWidget` and `SignalRowWidget` with `OphydLineEdit` and `OphydLabel`.

**Files:**
- Modify: `src/lucid/ui/widgets/signal_control.py`

- [ ] **Step 1: Replace imports**

Add:
```python
from lucid.epics.widgets.ophyd_lineedit import OphydLineEdit
from lucid.epics.widgets.ophyd_label import OphydLabel
```

- [ ] **Step 2: Migrate `SignalControlWidget._setup_ui`**

Replace the value display QLabel with OphydLabel:
```python
# Was: self._value_display = QLabel("---")
self._value_display = OphydLabel(precision=self._precision)
self._value_display.setStyleSheet("""
    font-size: 16pt;
    font-weight: bold;
    font-family: monospace;
    padding: 4px 8px;
""")
```

Replace the set QLineEdit with OphydLineEdit:
```python
# Was: self._set_edit = QLineEdit()
self._set_edit = OphydLineEdit(precision=self._precision, write_on_enter=True)
```

- [ ] **Step 3: Migrate `set_items` to bind signal to widgets**

In `set_items`, after determining `self._signal`, bind it:
```python
self._value_display.signal = self._signal
self._set_edit.signal = self._signal if self._writable else None
```

Remove the `_start_updates` / `_stop_updates` / `_update_timer` polling — OphydWidget handles subscription internally.

- [ ] **Step 4: Simplify `_update_display`**

The OphydLabel and OphydLineEdit update themselves. Reduce `_update_display` to only update metadata labels (status, kind, type). Remove value-reading logic.

- [ ] **Step 5: Simplify `_on_set_clicked`**

The OphydLineEdit already handles write-on-enter. The Set button can just call:
```python
def _on_set_clicked(self) -> None:
    if self._set_edit._modified:
        self._set_edit._write_current_value()
```

But we still want action logging. Connect to the OphydLineEdit's `value_written` signal:
```python
self._set_edit.value_written.connect(self._on_value_written)
```

```python
def _on_value_written(self, new_value: Any) -> None:
    action_logger = DeviceActionLogger.get_instance()
    action_logger.record_action(
        device_name=self._signal_name,
        action_type="set",
        old_value=self._value_display._value,
        new_value=new_value,
        unit=self._units,
    )
```

- [ ] **Step 6: Do the same for `SignalRowWidget`**

Replace `self._value_label` with `OphydLabel` and `self._set_edit` with `OphydLineEdit`. In the constructor, set `.signal = signal_obj`.

- [ ] **Step 7: Remove helper functions that are now handled by widgets**

Remove `_get_signal_value` and `_format_value` — these are now in OphydWidget/OphydLineEdit/OphydLabel. Keep `_put_signal_value` only if still needed for action logging.

- [ ] **Step 8: Commit**

```bash
git add src/lucid/ui/widgets/signal_control.py
git commit -m "refactor(signal_control): replace raw QLineEdit/QLabel with OphydLineEdit/OphydLabel"
```

---

### Task 10: Migrate `camera/base.py`

Replace raw QLineEdit and QComboBox in `CameraControlWidget` with ophyd widgets.

**Files:**
- Modify: `src/lucid/ui/widgets/camera/base.py`

- [ ] **Step 1: Replace imports and widget creation in `_setup_ui`**

```python
from lucid.epics.widgets.ophyd_lineedit import OphydLineEdit
from lucid.epics.widgets.ophyd_combobox import OphydComboBox
```

Replace in `_setup_ui`:
```python
self._acquire_time_edit = OphydLineEdit(precision=6, write_on_enter=True)
self._num_images_edit = OphydLineEdit(precision=0, write_on_enter=True)
self._image_mode_combo = OphydComboBox(write_on_change=True)
self._image_mode_combo.set_items(IMAGE_MODES)
self._shutter_combo = OphydComboBox(write_on_change=True)
self._shutter_combo.set_items(SHUTTER_MODES)
```

- [ ] **Step 2: Update `_connect_signals` to bind ophyd signals to widgets**

In `_on_connected` callback, bind cam signals to widgets:
```python
def _on_connected(result):
    initial_values, subscriptions = result
    self._subscriptions = subscriptions
    self._values.update(initial_values)

    cam = self._device.cam
    # Bind ophyd signals to widgets
    if hasattr(cam, "acquire_time"):
        self._acquire_time_edit.signal = cam.acquire_time
    if hasattr(cam, "num_images"):
        self._num_images_edit.signal = cam.num_images
    if hasattr(cam, "image_mode"):
        self._image_mode_combo.signal = cam.image_mode
    if hasattr(cam, "shutter_mode"):
        self._shutter_combo.signal = cam.shutter_mode

    self._status_indicator.set_state("on")
    self._status_label.setText("Connected")
    self._set_controls_enabled(True)
    self._connect_thread = None
```

Remove `acquire_time`, `num_images`, `image_mode` from the `signal_map` in `_background_connect` — the widgets handle their own subscriptions. Keep `acquire` and `detector_state` as manual subscriptions.

- [ ] **Step 3: Remove dead display/handler methods**

Delete:
- `_update_acquire_time_display`
- `_update_num_images_display`
- `_update_image_mode_display`
- `_update_shutter_mode_display`
- `_on_acquire_time_changed`
- `_on_num_images_changed`
- `_on_image_mode_changed`
- `_on_shutter_mode_changed`

- [ ] **Step 4: Update `_disconnect_signals` to clear widget signals**

```python
def _disconnect_signals(self) -> None:
    self._cancel_connect_thread()
    for signal, sub_id in self._subscriptions:
        try:
            signal.unsubscribe(sub_id)
        except Exception:
            pass
    self._subscriptions.clear()
    self._values.clear()
    # Disconnect ophyd widgets
    self._acquire_time_edit.signal = None
    self._num_images_edit.signal = None
    self._image_mode_combo.signal = None
    self._shutter_combo.signal = None
```

- [ ] **Step 5: Commit**

```bash
git add src/lucid/ui/widgets/camera/base.py
git commit -m "refactor(camera): replace raw QLineEdit/QComboBox with OphydLineEdit/OphydComboBox"
```

---

### Task 11: Migrate `camera/panels/cooler.py`

**Files:**
- Modify: `src/lucid/ui/widgets/camera/panels/cooler.py`

- [ ] **Step 1: Replace raw widgets**

Replace:
- `QComboBox` for cooler on/off → `OphydComboBox`
- `QDoubleSpinBox` for temperature setpoint → `OphydSpinBox`
- `QLabel` for temperature readback → `OphydLabel`

```python
from lucid.epics.widgets.ophyd_combobox import OphydComboBox
from lucid.epics.widgets.ophyd_spinbox import OphydSpinBox
from lucid.epics.widgets.ophyd_label import OphydLabel
```

- [ ] **Step 2: Bind signals to widgets in `set_device`**

After getting cam reference, bind:
```python
if hasattr(cam, "andor_cooler"):
    self._cooler_combo.signal = cam.andor_cooler
if hasattr(cam, "temperature_setpoint"):
    self._setpoint_spin.signal = cam.temperature_setpoint
if hasattr(cam, "temperature_actual"):
    self._temp_label.signal = cam.temperature_actual
```

- [ ] **Step 3: Remove manual subscription/callback code**

Delete the `_subscribe_signals`, `_on_value_update`, and per-field update methods.

- [ ] **Step 4: Commit**

```bash
git add src/lucid/ui/widgets/camera/panels/cooler.py
git commit -m "refactor(cooler): replace raw widgets with OphydComboBox/OphydSpinBox/OphydLabel"
```

---

### Task 12: Migrate `camera/panels/temperature.py`

**Files:**
- Modify: `src/lucid/ui/widgets/camera/panels/temperature.py`

- [ ] **Step 1: Replace raw widgets**

```python
from lucid.epics.widgets.ophyd_spinbox import OphydSpinBox
from lucid.epics.widgets.ophyd_label import OphydLabel
```

Replace `QDoubleSpinBox` with `OphydSpinBox`, `QLabel` for temp readback with `OphydLabel`.

- [ ] **Step 2: Bind signals**

```python
if hasattr(cam, "temperature"):
    self._temp_label.signal = cam.temperature
if hasattr(cam, "temperature_setpoint"):
    self._setpoint_spin.signal = cam.temperature_setpoint
```

- [ ] **Step 3: Remove manual subscription code**

- [ ] **Step 4: Commit**

```bash
git add src/lucid/ui/widgets/camera/panels/temperature.py
git commit -m "refactor(temperature): replace raw widgets with OphydSpinBox/OphydLabel"
```

---

### Task 13: Migrate `motor_control.py`

This is the most complex ophyd migration. The `MotorControlWidget` has raw QLineEdits for setpoint and tweak, and QLabels for readback/status, all driven by ophyd motor attribute polling.

**Files:**
- Modify: `src/lucid/ui/widgets/motor_control.py`

- [ ] **Step 1: Read the full file to understand current structure**

Read `motor_control.py` completely — it's ~1114 lines. Identify all raw widgets that are PV/signal-connected.

- [ ] **Step 2: Replace imports and widget creation**

Replace setpoint QLineEdit with OphydLineEdit, tweak QLineEdit with OphydLineEdit, readback QLabel with OphydLabel.

The motor's ophyd signals are:
- `motor.user_readback` → OphydLabel (RBV)
- `motor.user_setpoint` → OphydLineEdit (setpoint)

Tweak value doesn't have a standard ophyd signal — it may stay as a plain QLineEdit since it's a local UI value, not a signal readback.

- [ ] **Step 3: Bind signals in `set_items`**

```python
if hasattr(motor, "user_readback"):
    self._rbv_display.signal = motor.user_readback
if hasattr(motor, "user_setpoint"):
    self._setpoint_edit.signal = motor.user_setpoint
```

- [ ] **Step 4: Remove polling timer for signal-bound widgets**

Keep polling only for status fields that don't have subscriptions.

- [ ] **Step 5: Commit**

```bash
git add src/lucid/ui/widgets/motor_control.py
git commit -m "refactor(motor_control): replace raw QLineEdit/QLabel with OphydLineEdit/OphydLabel"
```

---

## Phase 4: Dedup StatusIndicator

### Task 14: Consolidate duplicated `StatusIndicator` class

`StatusIndicator` is copy-pasted in 4 files: `motor.py`, `areadetector/controls.py`, `camera/base.py`, `camera/panels/cooler.py`. Move to a shared location.

**Files:**
- Create: `src/lucid/epics/widgets/status_indicator.py`
- Modify: `src/lucid/epics/widgets/motor.py` — import from shared
- Modify: `src/lucid/epics/widgets/areadetector/controls.py` — import from shared
- Modify: `src/lucid/ui/widgets/camera/base.py` — import from shared
- Modify: `src/lucid/ui/widgets/camera/panels/cooler.py` — import from shared
- Modify: `src/lucid/ui/widgets/signal_control.py` — import StatusDot alias or convert to StatusIndicator

- [ ] **Step 1: Create shared `StatusIndicator`**

```python
# src/lucid/epics/widgets/status_indicator.py
"""StatusIndicator — small circular status dot for connection/alarm state."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QWidget

from lucid.epics.widgets.style import (
    get_success_color,
    get_error_color,
    get_warning_color,
    get_disconnected_color,
)


class StatusIndicator(QFrame):
    """A small circular status indicator.

    States: 'off', 'on', 'warning', 'error', 'disconnected'.
    """

    def __init__(self, size: int = 14, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._state = "off"
        self._radius = size // 2
        self._update_style()

    def set_state(self, state: str) -> None:
        self._state = state
        self._update_style()

    def set_connected(self, connected: bool) -> None:
        self.set_state("on" if connected else "error")

    def _update_style(self) -> None:
        colors = {
            "off": "#666666",
            "on": get_success_color(),
            "warning": get_warning_color(),
            "error": get_error_color(),
            "disconnected": get_disconnected_color(),
        }
        color = colors.get(self._state, colors["off"])
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: {self._radius}px;
                border: 1px solid #333;
            }}
        """)
```

- [ ] **Step 2: Replace all local StatusIndicator/StatusDot classes with import**

In each file, replace the local class with:
```python
from lucid.epics.widgets.status_indicator import StatusIndicator
```

For `signal_control.py`, replace `StatusDot` with `StatusIndicator` and update the 2 call sites (`set_color` → `set_state`, `set_connected` stays).

- [ ] **Step 3: Export from `__init__.py`**

```python
from lucid.epics.widgets.status_indicator import StatusIndicator
```

- [ ] **Step 4: Commit**

```bash
git add src/lucid/epics/widgets/status_indicator.py \
    src/lucid/epics/widgets/__init__.py \
    src/lucid/epics/widgets/motor.py \
    src/lucid/epics/widgets/areadetector/controls.py \
    src/lucid/ui/widgets/camera/base.py \
    src/lucid/ui/widgets/camera/panels/cooler.py \
    src/lucid/ui/widgets/signal_control.py
git commit -m "refactor: consolidate duplicated StatusIndicator into shared module"
```
