# Device Panel Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the LUCID Devices panel from a tree-with-detail-splitter to a tabbed interface with Favorites, All, and dynamic device controller tabs.

**Architecture:** DevicePanel becomes a thin coordinator owning a QTabWidget. Three new widgets handle the heavy lifting: DeviceTreeTab (the "All" tree view), FavoritesTab (compact motor widgets), and CompactMotorWidget (single motor row). Favorites persist per-beamline via PreferencesManager.

**Tech Stack:** PySide6, ophyd, qtawesome, loguru

**Spec:** `docs/superpowers/specs/2026-04-14-device-panel-tabs-design.md`

---

### Task 1: CompactMotorWidget

The standalone compact motor control row. No dependencies on the panel restructure — can be built and tested in isolation.

**Files:**
- Create: `ncs/src/lucid/ui/widgets/compact_motor.py`
- Test: `ncs/tests/test_compact_motor.py`

- [ ] **Step 1: Write the failing test for CompactMotorWidget**

```python
# ncs/tests/test_compact_motor.py
"""Tests for CompactMotorWidget."""

from unittest.mock import MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from lucid.devices.model import DeviceCategory, DeviceInfo, DeviceState, DeviceStatus


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def mock_device_info():
    """Create a mock motor DeviceInfo."""
    device_id = uuid4()
    info = MagicMock(spec=DeviceInfo)
    info.id = device_id
    info.name = "test_motor"
    info.device_class = "ophyd.sim.SynAxis"
    info.category = DeviceCategory.MOTOR
    info.metadata = {"units": "mm", "precision": 3}
    info.active = True
    info._state = DeviceState(
        device_id=device_id, status=DeviceStatus.ONLINE, connected=True
    )
    info._ophyd_device = None
    return info


@pytest.fixture
def mock_motor():
    """Create a mock ophyd motor."""
    motor = MagicMock()
    motor.name = "test_motor"
    motor.position = 10.0
    motor.moving = False
    motor.user_readback = MagicMock()
    motor.user_setpoint = MagicMock()
    motor.set = MagicMock()
    motor.stop = MagicMock()
    return motor


class TestCompactMotorWidget:
    def test_creation(self, qapp, mock_device_info, mock_motor):
        from lucid.ui.widgets.compact_motor import CompactMotorWidget

        widget = CompactMotorWidget(
            device_info=mock_device_info,
            ophyd_obj=mock_motor,
        )
        assert widget is not None
        # Should have name label showing device name
        assert widget._name_label.text() == "test_motor"
        widget.close()

    def test_jog_abs_toggle(self, qapp, mock_device_info, mock_motor):
        from lucid.ui.widgets.compact_motor import CompactMotorWidget

        widget = CompactMotorWidget(
            device_info=mock_device_info,
            ophyd_obj=mock_motor,
        )
        # Default mode is Abs
        assert widget._mode_btn.text() == "Abs"
        assert widget.is_jog_mode is False

        # Toggle to Jog
        widget._mode_btn.click()
        assert widget._mode_btn.text() == "Jog"
        assert widget.is_jog_mode is True

        # Toggle back to Abs
        widget._mode_btn.click()
        assert widget._mode_btn.text() == "Abs"
        assert widget.is_jog_mode is False
        widget.close()

    def test_abs_move(self, qapp, mock_device_info, mock_motor):
        from lucid.ui.widgets.compact_motor import CompactMotorWidget

        widget = CompactMotorWidget(
            device_info=mock_device_info,
            ophyd_obj=mock_motor,
        )
        # In Abs mode, entering a value and clicking Go should call motor.set(value)
        widget._setpoint_edit.setText("25.0")
        widget._go_btn.click()
        mock_motor.set.assert_called_once_with(25.0)
        widget.close()

    def test_jog_move(self, qapp, mock_device_info, mock_motor):
        from lucid.ui.widgets.compact_motor import CompactMotorWidget

        widget = CompactMotorWidget(
            device_info=mock_device_info,
            ophyd_obj=mock_motor,
        )
        # Switch to Jog mode
        widget._mode_btn.click()
        assert widget.is_jog_mode is True

        # current position is 10.0, jog by 5.0 should move to 15.0
        widget._setpoint_edit.setText("5.0")
        widget._go_btn.click()
        mock_motor.set.assert_called_once_with(15.0)
        widget.close()

    def test_stop(self, qapp, mock_device_info, mock_motor):
        from lucid.ui.widgets.compact_motor import CompactMotorWidget

        widget = CompactMotorWidget(
            device_info=mock_device_info,
            ophyd_obj=mock_motor,
        )
        widget._stop_btn.click()
        mock_motor.stop.assert_called_once()
        widget.close()

    def test_no_motor_shows_connecting(self, qapp, mock_device_info):
        """When ophyd_obj is None, widget should show connecting state."""
        from lucid.ui.widgets.compact_motor import CompactMotorWidget

        mock_device_info._state.status = DeviceStatus.CONNECTING
        widget = CompactMotorWidget(
            device_info=mock_device_info,
            ophyd_obj=None,
        )
        assert widget._go_btn.isEnabled() is False
        assert widget._stop_btn.isEnabled() is False
        widget.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest ncs/tests/test_compact_motor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lucid.ui.widgets.compact_motor'`

- [ ] **Step 3: Implement CompactMotorWidget**

```python
# ncs/src/lucid/ui/widgets/compact_motor.py
"""Compact motor control widget for favorites display.

Provides a single horizontal row with motor name, readback,
jog/abs toggle, setpoint entry, go button, and stop button.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from lucid.epics.widgets.ophyd_label import OphydLabel
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.devices.model import DeviceInfo


class CompactMotorWidget(QWidget):
    """Compact single-row motor control widget.

    Layout (left to right):
        Name | Readback | Jog/Abs toggle | Setpoint | Go | Stop

    Signals:
        open_controller_requested: Emitted with device_id when user wants
            to open the full controller tab.
        remove_favorite_requested: Emitted with device_id when user wants
            to remove this device from favorites.
        control_error: Emitted with error message string.
    """

    open_controller_requested = Signal(str)  # device_id
    remove_favorite_requested = Signal(str)  # device_id
    control_error = Signal(str)

    # Fixed height for compact appearance
    WIDGET_HEIGHT = 38

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

        self.setFixedHeight(self.WIDGET_HEIGHT)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self._setup_ui()
        self._bind_signals()
        self._update_state()

    @property
    def device_id(self) -> str:
        """Get the device ID as a string."""
        return str(self._device_info.id)

    @property
    def is_jog_mode(self) -> bool:
        """Whether the widget is in jog (relative) mode."""
        return self._is_jog_mode

    def _setup_ui(self) -> None:
        """Build the horizontal layout."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        # Motor name
        self._name_label = QLabel(self._device_info.name)
        self._name_label.setStyleSheet("font-weight: bold;")
        self._name_label.setFixedWidth(120)
        self._name_label.setToolTip(self._device_info.name)
        layout.addWidget(self._name_label)

        # Position readback
        precision = 4
        if self._device_info.metadata:
            precision = self._device_info.metadata.get("precision", 4)
        self._rbv_display = OphydLabel(precision=precision)
        self._rbv_display._value_label.setStyleSheet(
            "font-family: monospace; font-size: 10pt;"
        )
        self._rbv_display._value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._rbv_display.setMinimumWidth(80)
        layout.addWidget(self._rbv_display)

        # Jog/Abs toggle button
        self._mode_btn = QPushButton("Abs")
        self._mode_btn.setFixedWidth(40)
        self._mode_btn.setToolTip("Toggle between Absolute and Jog (relative) mode")
        self._mode_btn.setCheckable(True)
        self._mode_btn.clicked.connect(self._on_mode_toggled)
        layout.addWidget(self._mode_btn)

        # Setpoint / jog value entry
        self._setpoint_edit = QLineEdit()
        self._setpoint_edit.setValidator(QDoubleValidator())
        self._setpoint_edit.setPlaceholderText("Target")
        self._setpoint_edit.setMaximumWidth(100)
        self._setpoint_edit.returnPressed.connect(self._on_go_clicked)
        layout.addWidget(self._setpoint_edit)

        # Go button
        self._go_btn = QPushButton("Go")
        self._go_btn.setFixedWidth(36)
        self._go_btn.clicked.connect(self._on_go_clicked)
        layout.addWidget(self._go_btn)

        # Stop button
        self._stop_btn = QPushButton("\u25a0")  # filled square
        self._stop_btn.setFixedWidth(30)
        self._stop_btn.setToolTip("Stop motor")
        self._stop_btn.setStyleSheet("color: #F44336; font-weight: bold;")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self._stop_btn)

    def _bind_signals(self) -> None:
        """Bind OphydLabel to the motor readback signal."""
        if self._motor is None:
            return
        if hasattr(self._motor, "user_readback"):
            self._rbv_display.signal = self._motor.user_readback
        elif hasattr(self._motor, "readback"):
            self._rbv_display.signal = self._motor.readback

    def _unbind_signals(self) -> None:
        """Unbind ophyd signals."""
        self._rbv_display.signal = None

    def _update_state(self) -> None:
        """Update enabled/disabled state based on motor availability."""
        has_motor = self._motor is not None
        self._go_btn.setEnabled(has_motor)
        self._stop_btn.setEnabled(has_motor)
        self._setpoint_edit.setEnabled(has_motor)
        self._mode_btn.setEnabled(has_motor)

        if not has_motor:
            self._rbv_display._value_label.setText("...")
            self._name_label.setText(
                f"{self._device_info.name} (connecting...)"
            )

    def set_motor(self, ophyd_obj: Any) -> None:
        """Update the ophyd motor object (e.g., after connection).

        Args:
            ophyd_obj: The connected ophyd motor object.
        """
        self._unbind_signals()
        self._motor = ophyd_obj
        self._bind_signals()
        self._name_label.setText(self._device_info.name)
        self._update_state()

    def _get_current_position(self) -> float | None:
        """Get the current motor position."""
        if self._motor is None:
            return None
        if hasattr(self._motor, "position"):
            return self._motor.position
        if hasattr(self._motor, "readback") and hasattr(
            self._motor.readback, "get"
        ):
            return self._motor.readback.get()
        return None

    @Slot(bool)
    def _on_mode_toggled(self, checked: bool) -> None:
        """Handle jog/abs toggle."""
        self._is_jog_mode = checked
        if checked:
            self._mode_btn.setText("Jog")
            self._setpoint_edit.setPlaceholderText("Step")
        else:
            self._mode_btn.setText("Abs")
            self._setpoint_edit.setPlaceholderText("Target")

    @Slot()
    def _on_go_clicked(self) -> None:
        """Execute a move (absolute or jog)."""
        if self._motor is None:
            return
        try:
            value = float(self._setpoint_edit.text())
            if self._is_jog_mode:
                current = self._get_current_position()
                if current is None:
                    self.control_error.emit("Cannot read current position for jog")
                    return
                target = current + value
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
        """Stop the motor."""
        if self._motor is None:
            return
        try:
            if hasattr(self._motor, "stop"):
                self._motor.stop()
                logger.info("CompactMotor: stopped {}", self._device_info.name)
        except Exception as e:
            self.control_error.emit(f"Stop failed: {e}")
            logger.error("CompactMotor stop failed: {}", e)

    @Slot()
    def _on_context_menu(self, pos) -> None:
        """Show right-click context menu."""
        from PySide6.QtWidgets import QMenu

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
        """Clean up on close."""
        self._unbind_signals()
        super().closeEvent(event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest ncs/tests/test_compact_motor.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ncs && git add src/lucid/ui/widgets/compact_motor.py tests/test_compact_motor.py && git commit -m "feat: add CompactMotorWidget for favorites tab"
```

---

### Task 2: FavoritesTab

The favorites tab widget that manages a vertical list of CompactMotorWidgets. Depends on Task 1.

**Files:**
- Create: `ncs/src/lucid/ui/widgets/favorites_tab.py`
- Test: `ncs/tests/test_favorites_tab.py`

- [ ] **Step 1: Write the failing test for FavoritesTab**

```python
# ncs/tests/test_favorites_tab.py
"""Tests for FavoritesTab."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication

from lucid.devices.model import DeviceCategory, DeviceInfo, DeviceState, DeviceStatus


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _make_motor_info(name="motor_1"):
    device_id = uuid4()
    info = MagicMock(spec=DeviceInfo)
    info.id = device_id
    info.name = name
    info.device_class = "ophyd.sim.SynAxis"
    info.category = DeviceCategory.MOTOR
    info.metadata = {"units": "mm", "precision": 3}
    info.active = True
    info._state = DeviceState(
        device_id=device_id, status=DeviceStatus.ONLINE, connected=True
    )
    info._ophyd_device = MagicMock()
    info._ophyd_device.name = name
    info._ophyd_device.position = 0.0
    info._ophyd_device.user_readback = MagicMock()
    return info


@pytest.fixture
def mock_catalog():
    catalog = MagicMock()
    catalog.get_device.return_value = None
    return catalog


class TestFavoritesTab:
    def test_empty_state(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab

        tab = FavoritesTab(catalog=mock_catalog)
        # Should show placeholder text
        assert tab._placeholder.isVisible()
        assert len(tab._widgets) == 0
        tab.close()

    def test_add_favorite(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab

        info = _make_motor_info("motor_1")
        mock_catalog.get_device.return_value = info

        tab = FavoritesTab(catalog=mock_catalog)
        tab.add_favorite(str(info.id))

        assert len(tab._widgets) == 1
        assert tab._placeholder.isVisible() is False
        tab.close()

    def test_remove_favorite(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab

        info = _make_motor_info("motor_1")
        mock_catalog.get_device.return_value = info

        tab = FavoritesTab(catalog=mock_catalog)
        tab.add_favorite(str(info.id))
        assert len(tab._widgets) == 1

        tab.remove_favorite(str(info.id))
        assert len(tab._widgets) == 0
        assert tab._placeholder.isVisible()
        tab.close()

    def test_is_favorite(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab

        info = _make_motor_info("motor_1")
        mock_catalog.get_device.return_value = info

        tab = FavoritesTab(catalog=mock_catalog)
        assert tab.is_favorite(str(info.id)) is False

        tab.add_favorite(str(info.id))
        assert tab.is_favorite(str(info.id)) is True

        tab.remove_favorite(str(info.id))
        assert tab.is_favorite(str(info.id)) is False
        tab.close()

    def test_duplicate_add_is_noop(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab

        info = _make_motor_info("motor_1")
        mock_catalog.get_device.return_value = info

        tab = FavoritesTab(catalog=mock_catalog)
        tab.add_favorite(str(info.id))
        tab.add_favorite(str(info.id))
        assert len(tab._widgets) == 1
        tab.close()

    def test_get_favorite_ids(self, qapp, mock_catalog):
        from lucid.ui.widgets.favorites_tab import FavoritesTab

        info1 = _make_motor_info("motor_1")
        info2 = _make_motor_info("motor_2")

        def get_device(device_id):
            if device_id == info1.id:
                return info1
            if device_id == info2.id:
                return info2
            return None

        mock_catalog.get_device.side_effect = get_device

        tab = FavoritesTab(catalog=mock_catalog)
        tab.add_favorite(str(info1.id))
        tab.add_favorite(str(info2.id))

        ids = tab.get_favorite_ids()
        assert ids == [str(info1.id), str(info2.id)]
        tab.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest ncs/tests/test_favorites_tab.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lucid.ui.widgets.favorites_tab'`

- [ ] **Step 3: Implement FavoritesTab**

```python
# ncs/src/lucid/ui/widgets/favorites_tab.py
"""Favorites tab widget for the Devices panel.

Displays a vertical list of CompactMotorWidgets for favorited devices.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lucid.ui.widgets.compact_motor import CompactMotorWidget
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.devices.catalog import DeviceCatalog
    from lucid.devices.model import DeviceCategory


class FavoritesTab(QWidget):
    """Tab showing compact control widgets for favorited devices.

    Signals:
        open_controller_requested: device_id — user wants to open full controller.
        favorite_removed: device_id — user removed a favorite.
        favorites_changed: list[str] — the full favorites list changed (for persistence).
    """

    open_controller_requested = Signal(str)
    favorite_removed = Signal(str)
    favorites_changed = Signal(list)

    def __init__(
        self,
        catalog: DeviceCatalog,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._favorite_ids: list[str] = []  # ordered list of device IDs
        self._widgets: dict[str, CompactMotorWidget] = {}  # device_id -> widget

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the favorites tab UI."""
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area containing the list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        outer_layout.addWidget(self._scroll)

        # Inner container
        self._container = QWidget()
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(2)

        # Placeholder for empty state
        self._placeholder = QLabel(
            "Right-click a device in the All tab to add favorites"
        )
        self._placeholder.setStyleSheet("color: #888; font-style: italic; padding: 20px;")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._list_layout.addWidget(self._placeholder)

        self._list_layout.addStretch()
        self._scroll.setWidget(self._container)

    def is_favorite(self, device_id: str) -> bool:
        """Check if a device is in the favorites list."""
        return device_id in self._favorite_ids

    def get_favorite_ids(self) -> list[str]:
        """Get the ordered list of favorited device IDs."""
        return list(self._favorite_ids)

    def set_favorites(self, device_ids: list[str]) -> None:
        """Set the full favorites list (for loading from prefs).

        Args:
            device_ids: Ordered list of device ID strings.
        """
        # Clear existing
        for did in list(self._favorite_ids):
            self._remove_widget(did)
        self._favorite_ids.clear()

        # Add each
        for did in device_ids:
            self._add_widget(did)

        self._update_placeholder()

    def add_favorite(self, device_id: str) -> None:
        """Add a device to favorites.

        Args:
            device_id: The device ID string.
        """
        if device_id in self._favorite_ids:
            return

        self._add_widget(device_id)
        self._update_placeholder()
        self.favorites_changed.emit(self.get_favorite_ids())

    def remove_favorite(self, device_id: str) -> None:
        """Remove a device from favorites.

        Args:
            device_id: The device ID string.
        """
        if device_id not in self._favorite_ids:
            return

        self._remove_widget(device_id)
        self._update_placeholder()
        self.favorites_changed.emit(self.get_favorite_ids())
        self.favorite_removed.emit(device_id)

    def _add_widget(self, device_id: str) -> None:
        """Create and add a CompactMotorWidget for a device."""
        from lucid.devices.model import DeviceCategory

        info = self._catalog.get_device(device_id)
        if info is None:
            logger.warning("Favorite device {} not found in catalog, skipping", device_id)
            return

        # Only support motors for now
        if info.category != DeviceCategory.MOTOR:
            logger.debug("Skipping non-motor favorite: {} ({})", info.name, info.category)
            return

        ophyd_obj = getattr(info, "_ophyd_device", None)
        widget = CompactMotorWidget(
            device_info=info,
            ophyd_obj=ophyd_obj,
            parent=self._container,
        )
        widget.open_controller_requested.connect(self.open_controller_requested)
        widget.remove_favorite_requested.connect(self.remove_favorite)

        # Insert before the stretch at the end
        insert_idx = self._list_layout.count() - 1  # before stretch
        self._list_layout.insertWidget(insert_idx, widget)

        self._favorite_ids.append(device_id)
        self._widgets[device_id] = widget

    def _remove_widget(self, device_id: str) -> None:
        """Remove and destroy a CompactMotorWidget."""
        widget = self._widgets.pop(device_id, None)
        if widget is not None:
            self._list_layout.removeWidget(widget)
            widget.close()
            widget.deleteLater()

        if device_id in self._favorite_ids:
            self._favorite_ids.remove(device_id)

    def _update_placeholder(self) -> None:
        """Show/hide placeholder based on whether there are favorites."""
        self._placeholder.setVisible(len(self._widgets) == 0)

    @Slot(str)
    def on_device_connected(self, device_id_str: str) -> None:
        """Handle a device becoming connected — update its widget.

        Args:
            device_id_str: The device ID that connected.
        """
        widget = self._widgets.get(device_id_str)
        if widget is None:
            return

        info = self._catalog.get_device(device_id_str)
        if info is None:
            return

        ophyd_obj = getattr(info, "_ophyd_device", None)
        if ophyd_obj is not None:
            widget.set_motor(ophyd_obj)

    def closeEvent(self, event) -> None:
        """Clean up all widgets."""
        for widget in self._widgets.values():
            widget.close()
        self._widgets.clear()
        super().closeEvent(event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest ncs/tests/test_favorites_tab.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ncs && git add src/lucid/ui/widgets/favorites_tab.py tests/test_favorites_tab.py && git commit -m "feat: add FavoritesTab widget for favorites management"
```

---

### Task 3: DeviceTreeTab

Extract the tree view and its toolbar/search/filter from DevicePanel into a standalone widget. Depends on nothing new.

**Files:**
- Create: `ncs/src/lucid/ui/widgets/device_tree_tab.py`
- Test: `ncs/tests/test_device_tree_tab.py`

- [ ] **Step 1: Write the failing test for DeviceTreeTab**

```python
# ncs/tests/test_device_tree_tab.py
"""Tests for DeviceTreeTab."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication

from lucid.devices.model import DeviceCategory, DeviceInfo, DeviceState, DeviceStatus
from lucid.ui.models.device_tree import DeviceTreeItem, DeviceTreeModel, NodeType


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _make_catalog_with_motors():
    """Create a mock catalog with motor devices."""
    catalog = MagicMock()

    devices = []
    for name in ["motor_a", "motor_b"]:
        device_id = uuid4()
        info = MagicMock(spec=DeviceInfo)
        info.id = device_id
        info.name = name
        info.device_class = "ophyd.sim.SynAxis"
        info.category = DeviceCategory.MOTOR
        info.metadata = {}
        info.active = True
        info._state = DeviceState(
            device_id=device_id, status=DeviceStatus.ONLINE, connected=True
        )
        info._ophyd_device = None
        devices.append(info)

    catalog.get_all_devices.return_value = devices
    catalog.get_device.side_effect = lambda did: next(
        (d for d in devices if d.id == did), None
    )
    return catalog, devices


class TestDeviceTreeTab:
    def test_creation(self, qapp):
        from lucid.ui.widgets.device_tree_tab import DeviceTreeTab

        catalog, devices = _make_catalog_with_motors()
        with patch.object(DeviceTreeModel, "_poll_value_refresh"):
            tab = DeviceTreeTab(catalog=catalog)
            tab._model._value_timer.stop()

        assert tab._tree_view is not None
        assert tab._search_input is not None
        tab.close()

    def test_signals_exist(self, qapp):
        from lucid.ui.widgets.device_tree_tab import DeviceTreeTab

        catalog, devices = _make_catalog_with_motors()
        with patch.object(DeviceTreeModel, "_poll_value_refresh"):
            tab = DeviceTreeTab(catalog=catalog)
            tab._model._value_timer.stop()

        # Verify signals are defined
        assert hasattr(tab, "device_open_requested")
        assert hasattr(tab, "favorite_toggled")
        tab.close()

    def test_search_filters_tree(self, qapp):
        from lucid.ui.widgets.device_tree_tab import DeviceTreeTab

        catalog, devices = _make_catalog_with_motors()
        with patch.object(DeviceTreeModel, "_poll_value_refresh"):
            tab = DeviceTreeTab(catalog=catalog)
            tab._model._value_timer.stop()

        # Search should update the proxy model filter
        tab._search_input.setText("motor_a")
        assert tab._proxy_model.filterRegularExpression().pattern() == "motor_a"
        tab.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest ncs/tests/test_device_tree_tab.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lucid.ui.widgets.device_tree_tab'`

- [ ] **Step 3: Implement DeviceTreeTab**

```python
# ncs/src/lucid/ui/widgets/device_tree_tab.py
"""Device tree tab widget for the Devices panel.

Contains the tree view, toolbar, and search/filter UI previously
housed in DevicePanel directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import qtawesome as qta
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QToolBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from lucid.ui.models.device_tree import (
    DeviceFilterProxyModel,
    DeviceTreeItem,
    DeviceTreeModel,
    NodeType,
)
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtCore import QPoint

    from lucid.devices.catalog import DeviceCatalog


class DeviceTreeTab(QWidget):
    """The 'All' tab — device tree with toolbar and search/filter.

    Signals:
        device_open_requested: Emitted with DeviceTreeItem when user
            double-clicks a device to open its controller tab.
        favorite_toggled: Emitted with (device_id: str, is_favorite: bool)
            when user toggles favorite status via context menu.
        item_selected: Emitted with DeviceTreeItem on single selection.
        items_selected: Emitted with list[DeviceTreeItem] on selection change.
    """

    device_open_requested = Signal(object)  # DeviceTreeItem
    favorite_toggled = Signal(str, bool)  # device_id, is_favorite
    item_selected = Signal(object)  # DeviceTreeItem
    items_selected = Signal(list)  # list[DeviceTreeItem]

    def __init__(
        self,
        catalog: DeviceCatalog,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._is_favorite_fn: Any = None  # callable(device_id) -> bool

        # Create model and proxy
        self._model = DeviceTreeModel(catalog)
        self._proxy_model = DeviceFilterProxyModel()
        self._proxy_model.setSourceModel(self._model)

        self._setup_ui()
        self._connect_signals()

    def set_is_favorite_fn(self, fn) -> None:
        """Set a callback to check if a device is favorited.

        Args:
            fn: Callable taking device_id (str) and returning bool.
        """
        self._is_favorite_fn = fn

    @property
    def model(self) -> DeviceTreeModel:
        """The underlying device tree model."""
        return self._model

    @property
    def proxy_model(self) -> DeviceFilterProxyModel:
        """The filter proxy model."""
        return self._proxy_model

    @property
    def tree_view(self) -> QTreeView:
        """The tree view widget."""
        return self._tree_view

    def _setup_ui(self) -> None:
        """Build the tab UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)

        # Search and filter row
        filter_layout = QHBoxLayout()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search devices and signals...")
        self._search_input.setClearButtonEnabled(True)
        filter_layout.addWidget(self._search_input, stretch=1)

        # Kind filter dropdown
        self._kind_actions: dict[str, QAction] = {}
        default_visible = {"hinted", "normal"}

        kind_menu = QMenu(self)
        for kind in ["hinted", "normal", "config", "omitted"]:
            action = QAction(kind.title(), self)
            action.setCheckable(True)
            action.setChecked(kind in default_visible)
            action.setData(kind)
            action.triggered.connect(self._on_kind_filter_changed)
            self._kind_actions[kind] = action
            kind_menu.addAction(action)

        self._kind_button = QToolButton()
        self._kind_button.setText("Kind")
        self._kind_button.setToolTip("Filter by signal/device kind")
        self._kind_button.setMenu(kind_menu)
        self._kind_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        filter_layout.addWidget(self._kind_button)

        self._proxy_model.set_visible_kinds(default_visible)
        layout.addLayout(filter_layout)

        # Tree view
        self._tree_view = QTreeView()
        self._tree_view.setModel(self._proxy_model)
        self._tree_view.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._tree_view.setAlternatingRowColors(True)
        self._tree_view.setAnimated(True)
        self._tree_view.setExpandsOnDoubleClick(False)  # We handle double-click
        self._tree_view.setSortingEnabled(True)
        self._tree_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        # Header configuration
        header = self._tree_view.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for col in range(1, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._tree_view.setColumnWidth(0, 200)

        # Context menu
        self._tree_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._tree_view.customContextMenuRequested.connect(self._on_context_menu)

        # Double-click
        self._tree_view.doubleClicked.connect(self._on_double_clicked)

        layout.addWidget(self._tree_view)

        # Start collapsed
        self._tree_view.collapseAll()

    def _create_toolbar(self) -> QToolBar:
        """Create the toolbar with sync, expand/collapse, show disabled."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        # Sync
        sync_action = QAction(qta.icon("mdi6.sync"), "Sync", self)
        sync_action.setToolTip("Retry failed device connections and refresh the tree")
        sync_action.triggered.connect(self._sync_devices)
        toolbar.addAction(sync_action)

        toolbar.addSeparator()

        # Expand all
        expand_action = QAction(
            qta.icon("mdi6.arrow-expand-vertical"), "Expand All", self
        )
        expand_action.triggered.connect(lambda: self._tree_view.expandAll())
        toolbar.addAction(expand_action)

        # Collapse all
        collapse_action = QAction(
            qta.icon("mdi6.arrow-collapse-vertical"), "Collapse", self
        )
        collapse_action.triggered.connect(lambda: self._tree_view.collapseAll())
        toolbar.addAction(collapse_action)

        toolbar.addSeparator()

        # Toggle inactive
        self._show_inactive_action = QAction(
            qta.icon("mdi6.eye-closed"), "Show Disabled", self
        )
        self._show_inactive_action.setToolTip("Show or hide disabled devices")
        self._show_inactive_action.setCheckable(True)
        self._show_inactive_action.setChecked(False)
        self._show_inactive_action.toggled.connect(self._on_toggle_inactive)
        toolbar.addAction(self._show_inactive_action)

        return toolbar

    def _connect_signals(self) -> None:
        """Connect internal signals."""
        self._search_input.textChanged.connect(self._on_search_changed)
        self._tree_view.selectionModel().selectionChanged.connect(
            self._on_selection_changed
        )
        self._catalog.device_added.connect(self._on_device_changed)
        self._catalog.device_removed.connect(self._on_device_changed)

    # === Handlers ===

    @Slot(str)
    def _on_search_changed(self, text: str) -> None:
        """Handle search text change."""
        self._proxy_model.setFilterRegularExpression(text)
        if text:
            self._tree_view.expandAll()
        else:
            self._tree_view.collapseAll()

    @Slot()
    def _on_kind_filter_changed(self) -> None:
        """Handle kind filter menu change."""
        visible_kinds = {
            kind for kind, action in self._kind_actions.items() if action.isChecked()
        }
        if len(visible_kinds) == len(self._kind_actions):
            self._proxy_model.set_visible_kinds(None)
        else:
            self._proxy_model.set_visible_kinds(visible_kinds)

        if visible_kinds and len(visible_kinds) < len(self._kind_actions):
            self._tree_view.expandAll()
        else:
            self._tree_view.collapseAll()

    @Slot(bool)
    def _on_toggle_inactive(self, checked: bool) -> None:
        """Toggle visibility of inactive devices."""
        self._proxy_model.set_show_inactive(checked)
        icon_name = "mdi6.eye" if checked else "mdi6.eye-closed"
        self._show_inactive_action.setIcon(qta.icon(icon_name))

    @Slot()
    def _on_selection_changed(self) -> None:
        """Handle tree selection change."""
        selected_items = self._get_selected_items()
        if selected_items:
            self.item_selected.emit(selected_items[0])
        self.items_selected.emit(selected_items)

    def _on_double_clicked(self, proxy_index) -> None:
        """Handle double-click on tree item — open controller tab."""
        source_index = self._proxy_model.mapToSource(proxy_index)
        if not source_index.isValid():
            return
        item = source_index.internalPointer()
        if isinstance(item, DeviceTreeItem) and item.node_type == NodeType.DEVICE:
            self.device_open_requested.emit(item)

    def _on_context_menu(self, pos) -> None:
        """Handle right-click context menu."""
        from PySide6.QtWidgets import QApplication

        index = self._tree_view.indexAt(pos)
        device_info = None
        if index.isValid():
            source_index = self._proxy_model.mapToSource(index)
            if source_index.isValid():
                item = source_index.internalPointer()
                if item is not None and item.node_type == NodeType.DEVICE:
                    device_info = item.device_info

        is_editable = self._get_backend_editable()
        menu = QMenu(self._tree_view)

        if device_info is not None:
            # Favorite toggle
            device_id_str = str(device_info.id)
            is_fav = (
                self._is_favorite_fn(device_id_str)
                if self._is_favorite_fn
                else False
            )
            if is_fav:
                fav_action = menu.addAction("Remove from Favorites")
                fav_action.triggered.connect(
                    lambda: self.favorite_toggled.emit(device_id_str, False)
                )
            else:
                fav_action = menu.addAction("Add to Favorites")
                fav_action.triggered.connect(
                    lambda: self.favorite_toggled.emit(device_id_str, True)
                )
            menu.addSeparator()

            if is_editable:
                edit_action = menu.addAction("Edit...")
                edit_action.triggered.connect(
                    lambda: self._edit_device(device_info)
                )
                if device_info.active:
                    toggle_action = menu.addAction("Disable")
                    toggle_action.triggered.connect(
                        lambda: self._toggle_device_active(device_info, False)
                    )
                else:
                    toggle_action = menu.addAction("Enable")
                    toggle_action.triggered.connect(
                        lambda: self._toggle_device_active(device_info, True)
                    )
                menu.addSeparator()

            from PySide6.QtWidgets import QApplication

            copy_name_action = menu.addAction("Copy Name")
            copy_name_action.triggered.connect(
                lambda: QApplication.clipboard().setText(device_info.name)
            )
            copy_prefix_action = menu.addAction("Copy Prefix")
            copy_prefix_action.triggered.connect(
                lambda: QApplication.clipboard().setText(device_info.prefix or "")
            )

            if is_editable:
                menu.addSeparator()
                delete_action = menu.addAction("Delete")
                delete_action.triggered.connect(
                    lambda: self._delete_device(device_info)
                )
                menu.addSeparator()

        if is_editable:
            add_action = menu.addAction("Add New Device...")
            add_action.triggered.connect(self._add_new_device)

        if not menu.actions():
            return
        menu.exec(self._tree_view.viewport().mapToGlobal(pos))

    # === Device operations (carried over from DevicePanel) ===

    def _get_backend_editable(self) -> bool:
        backend = self._catalog.backend
        return backend is not None and backend.is_editable

    def _edit_device(self, device_info) -> None:
        from lucid.ui.dialogs.device_edit_dialog import DeviceEditDialog

        dialog = DeviceEditDialog(mode="edit", device=device_info, parent=self)
        if dialog.exec():
            values = dialog.get_values()
            device_info.display_name = values["display_name"]
            device_info.prefix = values["prefix"]
            device_info.beamline = values["beamline"]
            device_info.group = values["group"]
            device_info.icon_override = values["icon_override"]
            device_info.active = values["active"]
            device_info.metadata.update(values.get("extra_fields", {}))
            self._catalog.update_device(device_info)

    def _add_new_device(self) -> None:
        from lucid.devices.model import DeviceInfo
        from lucid.ui.dialogs.device_edit_dialog import DeviceEditDialog

        dialog = DeviceEditDialog(mode="create", parent=self)
        if dialog.exec():
            values = dialog.get_values()
            device = DeviceInfo(
                name=values["name"],
                device_class=values["device_class"],
                prefix=values["prefix"],
                beamline=values["beamline"],
                display_name=values["display_name"],
                group=values["group"],
                icon_override=values["icon_override"],
                active=values["active"],
                metadata=values.get("extra_fields", {}),
            )
            if not self._catalog.add_device(device):
                QMessageBox.warning(
                    self,
                    "Add Failed",
                    f"Failed to add device '{values['name']}'. It may already exist.",
                )

    def _delete_device(self, device_info) -> None:
        reply = QMessageBox.question(
            self,
            "Delete Device",
            f"Delete device '{device_info.name}'? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._catalog.remove_device(device_info.id)

    def _toggle_device_active(self, device_info, active: bool) -> None:
        device_info.active = active
        self._catalog.update_device(device_info)

    def _sync_devices(self) -> None:
        """Retry failed connections and refresh the tree."""
        from lucid.utils.threads import QThreadFuture

        for backend in self._catalog.backends.values():
            if hasattr(backend, "reset_failed_devices"):
                backend.reset_failed_devices()

        def _do_reconnect():
            return self._catalog.reconnect_failed_devices(timeout=5.0)

        def _on_done(result):
            connected, failed = result
            self._model.refresh()
            logger.info("Sync: {} connected, {} still offline", connected, failed)

        thread = QThreadFuture(
            _do_reconnect,
            callback_slot=_on_done,
            name="sync-devices",
        )
        thread.start()
        logger.info("Syncing devices...")

    def _on_device_changed(self, _: Any) -> None:
        self._model.refresh()

    # === Utilities ===

    def _get_selected_items(self) -> list[DeviceTreeItem]:
        """Get all currently selected DeviceTreeItems."""
        selection = self._tree_view.selectionModel().selectedIndexes()
        items: list[DeviceTreeItem] = []
        seen: set[int] = set()

        for proxy_index in selection:
            if proxy_index.column() != 0:
                continue
            source_index = self._proxy_model.mapToSource(proxy_index)
            item = source_index.internalPointer()
            if isinstance(item, DeviceTreeItem):
                item_id = id(item)
                if item_id not in seen:
                    seen.add(item_id)
                    items.append(item)
        return items

    def select_device_by_id(self, device_id: str) -> None:
        """Select a device in the tree by its device_id.

        Args:
            device_id: The device ID to select.
        """
        root_item = self._model.root_item
        target_item = self._find_device_item(root_item, device_id)
        if target_item is None:
            return

        source_index = self._model.index_for_item(target_item)
        if not source_index.isValid():
            return

        proxy_index = self._proxy_model.mapFromSource(source_index)
        if not proxy_index.isValid():
            return

        self._tree_view.selectionModel().clearSelection()
        self._tree_view.selectionModel().select(
            proxy_index,
            self._tree_view.selectionModel().SelectionFlag.Select
            | self._tree_view.selectionModel().SelectionFlag.Rows,
        )
        self._tree_view.scrollTo(proxy_index)
        self._tree_view.expand(proxy_index.parent())

    def _find_device_item(
        self, item: DeviceTreeItem, device_id: str
    ) -> DeviceTreeItem | None:
        if item.device_info is not None and str(item.device_info.id) == device_id:
            return item
        for i in range(item.child_count()):
            child = item.child(i)
            if child:
                result = self._find_device_item(child, device_id)
                if result:
                    return result
        return None

    def get_visible_kinds(self) -> set[str] | None:
        """Get the current kind filter set."""
        return self._proxy_model.get_visible_kinds()

    def get_search_text(self) -> str:
        """Get the current search text."""
        return self._search_input.text()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest ncs/tests/test_device_tree_tab.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ncs && git add src/lucid/ui/widgets/device_tree_tab.py tests/test_device_tree_tab.py && git commit -m "feat: extract DeviceTreeTab from DevicePanel"
```

---

### Task 4: Add device_favorites to PreferencesManager

One-line change to register the new beamline-specific preference key.

**Files:**
- Modify: `ncs/src/lucid/ui/preferences/manager.py:27-32`

- [ ] **Step 1: Add device_favorites to BEAMLINE_SPECIFIC_PREFS**

In `ncs/src/lucid/ui/preferences/manager.py`, add `"device_favorites"` to the `BEAMLINE_SPECIFIC_PREFS` set:

```python
# Change line 27-32 from:
BEAMLINE_SPECIFIC_PREFS = {
    "default_data_dir",
    "panel_layout",
    "plot_defaults",
    "acquisition_defaults",
}

# To:
BEAMLINE_SPECIFIC_PREFS = {
    "default_data_dir",
    "panel_layout",
    "plot_defaults",
    "acquisition_defaults",
    "device_favorites",
}
```

- [ ] **Step 2: Commit**

```bash
cd ncs && git add src/lucid/ui/preferences/manager.py && git commit -m "feat: add device_favorites as beamline-specific preference"
```

---

### Task 5: Rebuild DevicePanel as tab coordinator

The core restructure — replace DevicePanel's layout with a QTabWidget and wire up all the sub-widgets. Depends on Tasks 1-4.

**Files:**
- Modify: `ncs/src/lucid/ui/panels/device_panel.py` (full rewrite)
- Test: `ncs/tests/test_device_panel_tabs.py`

- [ ] **Step 1: Write the failing test for the tabbed DevicePanel**

```python
# ncs/tests/test_device_panel_tabs.py
"""Tests for the tabbed DevicePanel."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication, QTabBar, QTabWidget

from lucid.devices.model import DeviceCategory, DeviceInfo, DeviceState, DeviceStatus
from lucid.ui.models.device_tree import DeviceTreeItem, DeviceTreeModel, NodeType


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _make_catalog():
    catalog = MagicMock()
    device_id = uuid4()
    info = MagicMock(spec=DeviceInfo)
    info.id = device_id
    info.name = "test_motor"
    info.device_class = "ophyd.sim.SynAxis"
    info.category = DeviceCategory.MOTOR
    info.metadata = {}
    info.active = True
    info._state = DeviceState(
        device_id=device_id, status=DeviceStatus.ONLINE, connected=True
    )
    info._ophyd_device = MagicMock()
    info._ophyd_device.name = "test_motor"
    info._ophyd_device.position = 0.0
    info._ophyd_device.user_readback = MagicMock()
    catalog.get_all_devices.return_value = [info]
    catalog.get_device.return_value = info
    return catalog, info


@pytest.fixture
def prefs_manager():
    mgr = MagicMock()
    mgr.get.return_value = []
    return mgr


class TestDevicePanelTabs:
    def test_has_tab_widget(self, qapp, prefs_manager):
        from lucid.ui.panels.device_panel import DevicePanel

        catalog, info = _make_catalog()
        with patch("lucid.ui.panels.device_panel.DeviceCatalog") as mock_dc, \
             patch("lucid.ui.panels.device_panel.PreferencesManager") as mock_pm, \
             patch.object(DeviceTreeModel, "_poll_value_refresh"):
            mock_dc.get_instance.return_value = catalog
            mock_pm.get_instance.return_value = prefs_manager
            panel = DevicePanel()
            panel._tree_tab._model._value_timer.stop()

        assert isinstance(panel._tabs, QTabWidget)
        assert panel._tabs.count() >= 2
        assert panel._tabs.tabText(0) == "Favorites"
        assert panel._tabs.tabText(1) == "All"
        panel.close()

    def test_first_two_tabs_unclosable(self, qapp, prefs_manager):
        from lucid.ui.panels.device_panel import DevicePanel

        catalog, info = _make_catalog()
        with patch("lucid.ui.panels.device_panel.DeviceCatalog") as mock_dc, \
             patch("lucid.ui.panels.device_panel.PreferencesManager") as mock_pm, \
             patch.object(DeviceTreeModel, "_poll_value_refresh"):
            mock_dc.get_instance.return_value = catalog
            mock_pm.get_instance.return_value = prefs_manager
            panel = DevicePanel()
            panel._tree_tab._model._value_timer.stop()

        tab_bar = panel._tabs.tabBar()
        # Close buttons on first two tabs should be None
        assert tab_bar.tabButton(0, QTabBar.ButtonPosition.RightSide) is None
        assert tab_bar.tabButton(1, QTabBar.ButtonPosition.RightSide) is None
        panel.close()

    def test_open_device_tab(self, qapp, prefs_manager):
        from lucid.ui.panels.device_panel import DevicePanel

        catalog, info = _make_catalog()
        with patch("lucid.ui.panels.device_panel.DeviceCatalog") as mock_dc, \
             patch("lucid.ui.panels.device_panel.PreferencesManager") as mock_pm, \
             patch.object(DeviceTreeModel, "_poll_value_refresh"):
            mock_dc.get_instance.return_value = catalog
            mock_pm.get_instance.return_value = prefs_manager
            panel = DevicePanel()
            panel._tree_tab._model._value_timer.stop()

        # Create a mock DeviceTreeItem
        item = MagicMock(spec=DeviceTreeItem)
        item.name = "test_motor"
        item.node_type = NodeType.DEVICE
        item.device_info = info
        item.ophyd_obj = info._ophyd_device

        initial_count = panel._tabs.count()
        panel._open_device_tab(item)
        assert panel._tabs.count() == initial_count + 1
        assert panel._tabs.tabText(panel._tabs.count() - 1) == "test_motor"
        panel.close()

    def test_open_device_tab_focuses_existing(self, qapp, prefs_manager):
        from lucid.ui.panels.device_panel import DevicePanel

        catalog, info = _make_catalog()
        with patch("lucid.ui.panels.device_panel.DeviceCatalog") as mock_dc, \
             patch("lucid.ui.panels.device_panel.PreferencesManager") as mock_pm, \
             patch.object(DeviceTreeModel, "_poll_value_refresh"):
            mock_dc.get_instance.return_value = catalog
            mock_pm.get_instance.return_value = prefs_manager
            panel = DevicePanel()
            panel._tree_tab._model._value_timer.stop()

        item = MagicMock(spec=DeviceTreeItem)
        item.name = "test_motor"
        item.node_type = NodeType.DEVICE
        item.device_info = info
        item.ophyd_obj = info._ophyd_device

        panel._open_device_tab(item)
        count_after_first = panel._tabs.count()

        # Opening the same device again should NOT add a tab
        panel._open_device_tab(item)
        assert panel._tabs.count() == count_after_first
        panel.close()

    def test_close_device_tab(self, qapp, prefs_manager):
        from lucid.ui.panels.device_panel import DevicePanel

        catalog, info = _make_catalog()
        with patch("lucid.ui.panels.device_panel.DeviceCatalog") as mock_dc, \
             patch("lucid.ui.panels.device_panel.PreferencesManager") as mock_pm, \
             patch.object(DeviceTreeModel, "_poll_value_refresh"):
            mock_dc.get_instance.return_value = catalog
            mock_pm.get_instance.return_value = prefs_manager
            panel = DevicePanel()
            panel._tree_tab._model._value_timer.stop()

        item = MagicMock(spec=DeviceTreeItem)
        item.name = "test_motor"
        item.node_type = NodeType.DEVICE
        item.device_info = info
        item.ophyd_obj = info._ophyd_device

        panel._open_device_tab(item)
        device_id = str(info.id)
        assert device_id in panel._device_tabs

        # Close it via the tab close mechanism
        tab_index = panel._tabs.indexOf(panel._device_tabs[device_id])
        panel._on_tab_close_requested(tab_index)
        assert device_id not in panel._device_tabs
        panel.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest ncs/tests/test_device_panel_tabs.py -v`
Expected: FAIL (DevicePanel doesn't have `_tabs`, `_tree_tab`, etc.)

- [ ] **Step 3: Rewrite DevicePanel as tab coordinator**

Replace the entire `ncs/src/lucid/ui/panels/device_panel.py` with the new tabbed implementation. The `DeviceOverviewWidget` class is removed (no longer needed). The key changes:

- `_setup_ui` creates a `QTabWidget` with Favorites (tab 0) and All (tab 1)
- Hides close buttons on tabs 0 and 1
- `_open_device_tab(item)` creates a new controller tab or focuses existing
- `_on_tab_close_requested(index)` ignores indices 0/1, destroys controller tabs
- Favorites persistence via `PreferencesManager.get/set("device_favorites")`
- `_on_favorite_toggled(device_id, is_favorite)` adds/removes favorites and saves
- Wires up `DeviceTreeTab.device_open_requested` → `_open_device_tab`
- Wires up `DeviceTreeTab.favorite_toggled` → `_on_favorite_toggled`
- Wires up `FavoritesTab.open_controller_requested` → `_open_device_tab_by_id`
- Preserves existing event handling (`DeviceSelectEvent`, `DeviceFocusEvent`)
- Preserves introspection API and panel actions

```python
# ncs/src/lucid/ui/panels/device_panel.py
"""Device management panel for NCS.

Provides a tabbed panel for viewing and managing devices:
- Favorites tab: compact motor control widgets for favorited devices
- All tab: full device tree with search and filtering
- Device tabs: individual device controller widgets opened on demand
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import QCoreApplication, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lucid.devices import DeviceCatalog
from lucid.ui.events import DeviceFocusEvent, DeviceSelectEvent
from lucid.ui.models.device_tree import DeviceTreeItem, DeviceTreeModel, NodeType
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.panels.registry import PanelRegistry
from lucid.ui.preferences.manager import PreferencesManager
from lucid.ui.widgets.device_control import DeviceControlWidget
from lucid.ui.widgets.device_tree_tab import DeviceTreeTab
from lucid.ui.widgets.favorites_tab import FavoritesTab
from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


class DevicePanel(BasePanel):
    """Tabbed panel for device management.

    Tab layout:
    - Tab 0: Favorites (unclosable) — compact motor widgets
    - Tab 1: All (unclosable) — device tree with search/filter
    - Tab 2+: Device controllers (closable) — opened on demand
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.devices",
        name="Devices",
        description="View and manage control system devices",
        icon="mdi.microwave",
        category="Core",
        required_permission=None,
        singleton=True,
        closable=True,
        keywords=["device", "motor", "detector", "hardware", "equipment", "signal"],
        default_area="left",
        sidebar_group="top",
        auto_hide=True,
        sidebar_order=1,
    )

    # Signals (preserved for backward compat)
    item_selected = Signal(object)  # DeviceTreeItem
    items_selected = Signal(list)  # list[DeviceTreeItem]

    def __init__(self, parent: QWidget | None = None) -> None:
        logger.info("DevicePanel.__init__() START")
        self._catalog = DeviceCatalog.get_instance()
        self._prefs = PreferencesManager.get_instance()

        # Track open device controller tabs: device_id -> widget
        self._device_tabs: dict[str, QWidget] = {}

        super().__init__(parent)

        # Load saved favorites
        self._load_favorites()

        # Connect catalog signals for favorites updates
        self._catalog.device_connected.connect(self._favorites_tab.on_device_connected)

        logger.info("DevicePanel.__init__() END")

    def _setup_ui(self) -> None:
        """Setup the tabbed panel UI."""
        # Main tab widget
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self._layout.addWidget(self._tabs)

        # Tab 0: Favorites
        self._favorites_tab = FavoritesTab(catalog=self._catalog)
        self._favorites_tab.open_controller_requested.connect(
            self._open_device_tab_by_id
        )
        self._favorites_tab.favorites_changed.connect(self._save_favorites)
        self._tabs.addTab(self._favorites_tab, "Favorites")

        # Tab 1: All (device tree)
        self._tree_tab = DeviceTreeTab(catalog=self._catalog)
        self._tree_tab.set_is_favorite_fn(self._favorites_tab.is_favorite)
        self._tree_tab.device_open_requested.connect(self._open_device_tab)
        self._tree_tab.favorite_toggled.connect(self._on_favorite_toggled)
        self._tree_tab.item_selected.connect(self.item_selected)
        self._tree_tab.items_selected.connect(self.items_selected)
        self._tabs.addTab(self._tree_tab, "All")

        # Hide close buttons on first two tabs
        tab_bar = self._tabs.tabBar()
        tab_bar.setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
        tab_bar.setTabButton(1, QTabBar.ButtonPosition.RightSide, None)

    # === Favorites ===

    def _load_favorites(self) -> None:
        """Load favorites from preferences."""
        saved = self._prefs.get("device_favorites", [])
        if saved:
            self._favorites_tab.set_favorites(saved)

    @Slot(list)
    def _save_favorites(self, favorite_ids: list[str]) -> None:
        """Save favorites to preferences."""
        self._prefs.set("device_favorites", favorite_ids)

    @Slot(str, bool)
    def _on_favorite_toggled(self, device_id: str, is_favorite: bool) -> None:
        """Handle favorite toggle from tree context menu."""
        if is_favorite:
            self._favorites_tab.add_favorite(device_id)
        else:
            self._favorites_tab.remove_favorite(device_id)

    # === Device Controller Tabs ===

    @Slot(object)
    def _open_device_tab(self, item: DeviceTreeItem) -> None:
        """Open a device controller in a new tab (or focus existing).

        Args:
            item: The DeviceTreeItem to open.
        """
        if item.device_info is None:
            return

        device_id = str(item.device_info.id)

        # If already open, focus it
        if device_id in self._device_tabs:
            widget = self._device_tabs[device_id]
            idx = self._tabs.indexOf(widget)
            if idx >= 0:
                self._tabs.setCurrentIndex(idx)
            return

        # Create a new DeviceControlWidget for this device
        control = DeviceControlWidget()
        control.set_items([item])
        control.control_error.connect(self._on_control_error)

        # Add the tab
        self._tabs.addTab(control, item.name)
        self._device_tabs[device_id] = control

        # Focus the new tab
        self._tabs.setCurrentWidget(control)

    def _open_device_tab_by_id(self, device_id: str) -> None:
        """Open a device controller tab by device ID.

        Used by FavoritesTab when "Open Controller" is requested.

        Args:
            device_id: The device ID string.
        """
        # If already open, focus
        if device_id in self._device_tabs:
            widget = self._device_tabs[device_id]
            idx = self._tabs.indexOf(widget)
            if idx >= 0:
                self._tabs.setCurrentIndex(idx)
            return

        # Find the device in the tree model and open
        root = self._tree_tab.model.root_item
        item = self._tree_tab._find_device_item(root, device_id)
        if item is not None:
            self._open_device_tab(item)

    @Slot(int)
    def _on_tab_close_requested(self, index: int) -> None:
        """Handle tab close request — ignore for tabs 0 and 1."""
        if index < 2:
            return  # Favorites and All are unclosable

        widget = self._tabs.widget(index)

        # Find and remove from tracking dict
        device_id_to_remove = None
        for device_id, w in self._device_tabs.items():
            if w is widget:
                device_id_to_remove = device_id
                break

        if device_id_to_remove is not None:
            del self._device_tabs[device_id_to_remove]

        # Remove and destroy
        self._tabs.removeTab(index)
        if widget is not None:
            widget.close()
            widget.deleteLater()

    @Slot(str)
    def _on_control_error(self, message: str) -> None:
        """Handle control error from a device controller tab."""
        logger.warning("Device control error: {}", message)

    # === Event Handling (preserved) ===

    def event(self, event) -> bool:
        if event.type() == DeviceSelectEvent.EventType:
            self._handle_device_select_event(event)
            return True
        if event.type() == DeviceFocusEvent.EventType:
            self._handle_device_focus_event(event)
            return True
        return super().event(event)

    def _handle_device_select_event(self, event: DeviceSelectEvent) -> None:
        self._tree_tab.select_device_by_id(event.device_id)

    def _handle_device_focus_event(self, event: DeviceFocusEvent) -> None:
        self._tree_tab.select_device_by_id(event.device_id)

    # === Introspection (preserved) ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get device panel-specific introspection data."""
        return {
            "active_tab": self._tabs.tabText(self._tabs.currentIndex()),
            "tab_count": self._tabs.count(),
            "open_device_tabs": list(self._device_tabs.keys()),
            "favorites_count": len(self._favorites_tab.get_favorite_ids()),
            "search_text": self._tree_tab.get_search_text(),
            "kind_filter": (
                list(self._tree_tab.get_visible_kinds())
                if self._tree_tab.get_visible_kinds()
                else None
            ),
            "device_count": self._tree_tab.model.rowCount(),
            "catalog_connected": self._catalog.is_connected,
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        actions = super()._get_available_actions()
        actions.extend([
            {
                "name": "refresh",
                "description": "Refresh the device tree",
                "method": "action_refresh",
            },
            {
                "name": "search",
                "description": "Search for devices/signals",
                "method": "action_search",
                "parameters": {"query": "string"},
            },
            {
                "name": "expand_all",
                "description": "Expand entire tree",
                "method": "action_expand_all",
            },
            {
                "name": "collapse_all",
                "description": "Collapse entire tree",
                "method": "action_collapse_all",
            },
        ])
        return actions

    def action_refresh(self) -> bool:
        self._tree_tab.model.refresh()
        return True

    def action_search(self, query: str) -> bool:
        self._tree_tab._search_input.setText(query)
        return True

    def action_expand_all(self) -> bool:
        self._tree_tab.tree_view.expandAll()
        return True

    def action_collapse_all(self) -> bool:
        self._tree_tab.tree_view.collapseAll()
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest ncs/tests/test_device_panel_tabs.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run existing device panel tests to check for regressions**

Run: `.venv/Scripts/python.exe -m pytest ncs/tests/test_device_tree_model.py ncs/tests/test_device_editing.py -v`
Expected: Existing tests still PASS (they test model/editing, not panel layout)

- [ ] **Step 6: Commit**

```bash
cd ncs && git add src/lucid/ui/panels/device_panel.py tests/test_device_panel_tabs.py && git commit -m "feat: rebuild DevicePanel as tabbed interface with Favorites, All, and device controller tabs"
```

---

### Task 6: Integration verification

Run the full test suite and visually verify the panel works.

**Files:** None (verification only)

- [ ] **Step 1: Run all new tests together**

Run: `.venv/Scripts/python.exe -m pytest ncs/tests/test_compact_motor.py ncs/tests/test_favorites_tab.py ncs/tests/test_device_tree_tab.py ncs/tests/test_device_panel_tabs.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run the full ncs test suite**

Run: `.venv/Scripts/python.exe -m pytest ncs/tests/ -v --timeout=30`
Expected: No new failures introduced

- [ ] **Step 3: Launch the app and verify visually**

Run: `.venv/Scripts/python.exe -m lucid`

Verify:
1. Device panel shows Favorites tab first, All tab second
2. Favorites and All tabs have no close buttons
3. Right-clicking a device in All shows "Add to Favorites" option
4. Adding a favorite shows a compact motor widget in Favorites tab
5. Double-clicking a device in All opens a controller tab
6. Double-clicking the same device focuses the existing tab
7. Controller tabs can be closed
8. Right-clicking a compact widget in Favorites shows "Open Controller" and "Remove from Favorites"
