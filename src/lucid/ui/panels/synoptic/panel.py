"""Synoptic panel for 2D beamline visualization.

This module provides SynopticPanel, a BasePanel subclass that displays
a 2D synoptic view of beamline hardware with interactive editing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import QCoreApplication, QEvent, Qt, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QSplitter,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lucid.auth.policy import Permission
from lucid.devices import DeviceCatalog
from lucid.ui.events import DeviceFocusEvent, DeviceSelectEvent
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.panels.registry import PanelRegistry
from lucid.ui.panels.synoptic.editors import SynopticPropertyEditor, TransformGizmo
from lucid.ui.panels.synoptic.items import BeamPath2DItem, Device2DItem
from lucid.ui.panels.synoptic.models import (
    BeamPathSegment,
    DeviceSynopticData,
    ViewPreset,
)
from lucid.ui.panels.synoptic.serialization import (
    DeviceSynopticSaver,
    SynopticPersistence,
    get_or_create_device_synoptic_data,
)
from lucid.ui.panels.synoptic.view import SynopticView
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.devices.model import DeviceInfo


class SynopticPanel(BasePanel):
    """Panel for 2D synoptic view of beamline hardware.

    Features:
    - 2D visualization of devices as shapes (rectangle, ellipse)
    - View presets as 2D projections:
      - Side: X-Z plane (beam direction × height)
      - Top: X-Y plane (beam direction × lateral)
      - Front: Y-Z plane (lateral × height)
    - Device selection with property editing (edit mode)
    - Beam path visualization
    - Permission-gated editing (DEVICE_CONFIGURE)

    Signals:
        device_selected: Emitted when device is clicked (device_id).
        device_focused: Emitted when device is double-clicked (device_id).
        edit_mode_changed: Emitted when edit mode changes (enabled).
    """

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.synoptic",
        name="Synoptic",
        description="2D visualization of beamline hardware layout",
        icon="layer-group",
        category="Core",
        required_permission=None,  # View is open to all authenticated
        singleton=True,
        closable=True,
        keywords=["2d", "synoptic", "layout", "beamline", "visualization", "hardware"],
        # Docking preferences - bottom sidebar (auto-hide icons on bottom edge)
        default_area="bottom",
        sidebar_group="top",
        auto_hide=True,
        sidebar_order=3,
    )

    # Signals
    device_selected = Signal(str)  # device_id
    device_focused = Signal(str)  # device_id (double-click)
    edit_mode_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the synoptic panel."""
        self._catalog = DeviceCatalog.get_instance()
        self._edit_mode = False
        self._current_beamline: str | None = None

        # Device tracking
        self._device_items: dict[str, Device2DItem] = {}
        self._device_info_map: dict[str, DeviceInfo] = {}
        self._selecting_from_event = False  # Prevent recursive event posting

        # Persistence
        self._persistence: SynopticPersistence | None = None
        self._saver = DeviceSynopticSaver(debounce_ms=500)

        super().__init__(parent)

        # Connect catalog signals
        self._catalog.device_added.connect(self._on_device_added)
        self._catalog.device_removed.connect(self._on_device_removed)

    def _setup_ui(self) -> None:
        """Setup the panel UI."""
        # Main horizontal splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._layout.addWidget(splitter)

        # Left side: view container
        view_container = QWidget()
        view_layout = QVBoxLayout(view_container)
        view_layout.setContentsMargins(0, 0, 0, 0)
        view_layout.setSpacing(0)

        # Toolbar
        self._toolbar = self._create_toolbar()
        view_layout.addWidget(self._toolbar)

        # 2D View - created directly (no deferred init needed)
        self._view = SynopticView()
        self._view.setMinimumSize(400, 300)
        view_layout.addWidget(self._view, stretch=1)

        # Connect view signals
        self._view.device_clicked.connect(self._on_device_clicked)
        self._view.device_double_clicked.connect(self._on_device_double_clicked)
        self._view.selection_changed.connect(self._on_selection_changed)
        self._view.view_changed.connect(self._on_view_changed)
        self._view.device_moved.connect(self._on_device_moved)

        # Status bar
        self._status_bar = self._create_status_bar()
        view_layout.addWidget(self._status_bar)

        splitter.addWidget(view_container)

        # Right side: property panel
        self._property_editor = SynopticPropertyEditor()
        self._property_editor.setMinimumWidth(200)
        self._property_editor.setMaximumWidth(300)
        self._property_editor.data_changed.connect(self._on_property_data_changed)
        splitter.addWidget(self._property_editor)

        # Set initial splitter sizes
        splitter.setSizes([600, 250])

        # Create gizmo and beam path items
        self._gizmo = TransformGizmo()
        self._beam_path = BeamPath2DItem()

        # Add items to view
        self._view.addItem(self._gizmo)
        self._gizmo.hide()
        self._view.addItem(self._beam_path)

        # Initialize persistence and load state
        self._init_persistence()

    def _init_persistence(self) -> None:
        """Initialize persistence handler and restore state."""
        try:
            from lucid.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
            self._current_beamline = prefs.get("current_beamline", "default")
        except Exception:
            self._current_beamline = "default"

        self._persistence = SynopticPersistence(self._current_beamline)

        # Block signals during state restoration
        self._view.blockSignals(True)
        try:
            # Load saved view state
            state = self._persistence.load_view_state()
            if state:
                self._view.restore_view_state(state)
                # Update gizmo and beam path to match preset
                self._gizmo.set_view_preset(state.view_preset)
                self._beam_path.set_view_preset(state.view_preset)

            # Load beam path
            segments = self._persistence.load_beam_path()
            if segments:
                self._beam_path.set_segments(segments)

            # Load devices
            self._load_devices()
        finally:
            self._view.blockSignals(False)

        # Update toolbar to reflect current state
        self._update_toolbar_state()

    def _create_toolbar(self) -> QToolBar:
        """Create the panel toolbar."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(toolbar.iconSize() * 0.8)

        # View preset dropdown (2D projections only)
        self._view_preset_combo = QComboBox()
        self._view_preset_combo.addItem("Side (X-Z)", ViewPreset.SIDE)
        self._view_preset_combo.addItem("Top (X-Y)", ViewPreset.TOP)
        self._view_preset_combo.addItem("Front (Y-Z)", ViewPreset.FRONT)
        self._view_preset_combo.setCurrentIndex(0)
        self._view_preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        toolbar.addWidget(self._view_preset_combo)

        toolbar.addSeparator()

        # Grid toggle
        self._grid_action = QAction("Grid", self)
        self._grid_action.setCheckable(True)
        self._grid_action.setChecked(True)
        self._grid_action.setToolTip("Toggle grid (G)")
        self._grid_action.setShortcut(QKeySequence("G"))
        self._grid_action.triggered.connect(self._on_grid_toggled)
        toolbar.addAction(self._grid_action)

        # Labels toggle
        self._labels_action = QAction("Labels", self)
        self._labels_action.setCheckable(True)
        self._labels_action.setChecked(True)
        self._labels_action.setToolTip("Toggle device labels")
        self._labels_action.triggered.connect(self._on_labels_toggled)
        toolbar.addAction(self._labels_action)

        toolbar.addSeparator()

        # Edit mode toggle (permission-gated)
        self._edit_action = QAction("Edit", self)
        self._edit_action.setCheckable(True)
        self._edit_action.setChecked(False)
        self._edit_action.setToolTip("Enter edit mode (requires permission)")
        self._edit_action.triggered.connect(self._on_edit_toggled)
        toolbar.addAction(self._edit_action)

        # Check edit permission and hide if not allowed
        self._update_edit_permission()

        toolbar.addSeparator()

        # Add device button (only in edit mode)
        self._add_device_button = QToolButton()
        self._add_device_button.setText("+Device")
        self._add_device_button.setToolTip("Add device to synoptic view")
        self._add_device_button.clicked.connect(self._on_add_device_clicked)
        self._add_device_button.setEnabled(False)
        toolbar.addWidget(self._add_device_button)

        # Add beam segment button
        self._add_beam_button = QToolButton()
        self._add_beam_button.setText("+Beam")
        self._add_beam_button.setToolTip("Add beam path segment")
        self._add_beam_button.clicked.connect(self._on_add_beam_clicked)
        self._add_beam_button.setEnabled(False)
        toolbar.addWidget(self._add_beam_button)

        return toolbar

    def _create_status_bar(self) -> QWidget:
        """Create the status bar widget."""
        status = QWidget()
        status.setFixedHeight(24)
        layout = QHBoxLayout(status)
        layout.setContentsMargins(8, 2, 8, 2)

        self._mode_label = QLabel("View Mode")
        layout.addWidget(self._mode_label)

        layout.addStretch()

        self._device_count_label = QLabel("Devices: 0")
        layout.addWidget(self._device_count_label)

        layout.addWidget(QLabel("|"))

        self._view_label = QLabel("View: Side (X-Z)")
        layout.addWidget(self._view_label)

        return status

    def _update_edit_permission(self) -> None:
        """Update edit button visibility based on user permission."""
        try:
            from lucid.auth.session import SessionManager

            session = SessionManager.get_instance()
            user = session.current_user

            if user is None:
                self._edit_action.setVisible(False)
                return

            can_edit = session.policy_engine.check_permission(
                user, Permission.DEVICE_CONFIGURE
            )
            self._edit_action.setVisible(can_edit)

            if not can_edit and self._edit_mode:
                self._set_edit_mode(False)

        except Exception as e:
            logger.warning("Failed to check edit permission: {}", e)
            self._edit_action.setVisible(False)

    def _set_edit_mode(self, enabled: bool) -> None:
        """Set edit mode state.

        Args:
            enabled: Whether to enable edit mode.
        """
        if self._edit_mode == enabled:
            return

        self._edit_mode = enabled
        self._edit_action.setChecked(enabled)
        self._property_editor.set_edit_mode(enabled)
        self._view.set_edit_mode(enabled)  # Enable drag in view
        self._add_device_button.setEnabled(enabled)
        self._add_beam_button.setEnabled(enabled)

        self._mode_label.setText("Edit Mode" if enabled else "View Mode")

        if enabled:
            self._update_gizmo()
        else:
            self._gizmo.hide()

        self.edit_mode_changed.emit(enabled)
        logger.debug("Synoptic edit mode: {}", "enabled" if enabled else "disabled")

    def _load_devices(self) -> None:
        """Load devices from catalog into view."""
        # Clear existing
        self._view.clear_device_items()
        self._device_items.clear()
        self._device_info_map.clear()

        # Load all devices with synoptic data
        for device_info in self._catalog.list_devices():
            self._add_device_to_view(device_info)

        self._update_device_count()

    def _add_device_to_view(self, device_info: DeviceInfo) -> None:
        """Add a device to the 2D view.

        Args:
            device_info: Device to add.
        """
        device_id = str(device_info.id)

        # Get or create synoptic data
        synoptic_data = get_or_create_device_synoptic_data(device_info)

        # Only add devices that have synoptic data with non-zero position
        # or are explicitly configured
        if not self._has_synoptic_config(device_info):
            return

        # Create device item with current view preset
        item = Device2DItem(
            device_id,
            device_info.name,
            synoptic_data,
            view_preset=self._view.get_view_preset(),
        )
        self._device_items[device_id] = item
        self._device_info_map[device_id] = device_info
        self._view.add_device_item(device_id, item)

    def _has_synoptic_config(self, device_info: DeviceInfo) -> bool:
        """Check if device has synoptic configuration.

        Args:
            device_info: Device to check.

        Returns:
            True if device has synoptic data configured.
        """
        return "synoptic" in device_info.metadata

    def _update_device_count(self) -> None:
        """Update device count in status bar."""
        count = len(self._device_items)
        self._device_count_label.setText(f"Devices: {count}")

    def _update_toolbar_state(self) -> None:
        """Update toolbar widgets to match view state."""
        self._grid_action.setChecked(self._view.is_grid_visible())

        # Update combo box to match current preset
        preset = self._view.get_view_preset()

        # Map legacy 3D presets to SIDE
        if preset in (ViewPreset.ORTHO3D, ViewPreset.PERSPECTIVE):
            preset = ViewPreset.SIDE

        for i in range(self._view_preset_combo.count()):
            if self._view_preset_combo.itemData(i) == preset:
                self._view_preset_combo.blockSignals(True)
                self._view_preset_combo.setCurrentIndex(i)
                self._view_preset_combo.blockSignals(False)
                break

        # Update view label based on preset
        preset_labels = {
            ViewPreset.SIDE: "Side (X-Z)",
            ViewPreset.TOP: "Top (X-Y)",
            ViewPreset.FRONT: "Front (Y-Z)",
        }
        label = preset_labels.get(preset, "Side (X-Z)")
        self._view_label.setText(f"View: {label}")

    def _update_gizmo(self) -> None:
        """Update transform gizmo visibility and position."""
        if not self._edit_mode:
            self._gizmo.hide()
            return

        selected = self._view.get_selected_device_ids()
        if len(selected) == 1:
            device_id = selected[0]
            item = self._device_items.get(device_id)
            if item:
                self._gizmo.show_at_device(item)
                return

        self._gizmo.hide()

    # === Toolbar Event Handlers ===

    @Slot(int)
    def _on_preset_changed(self, index: int) -> None:
        """Handle view preset selection."""
        preset = self._view_preset_combo.currentData()
        if preset:
            self._view.apply_view_preset(preset)
            # Update gizmo and beam path to use new projection
            self._gizmo.set_view_preset(preset)
            self._beam_path.set_view_preset(preset)
            self._update_toolbar_state()

    @Slot(bool)
    def _on_grid_toggled(self, checked: bool) -> None:
        """Handle grid toggle."""
        self._view.set_grid_visible(checked)

    @Slot(bool)
    def _on_labels_toggled(self, checked: bool) -> None:
        """Handle labels toggle."""
        self._view.set_labels_visible(checked)

    @Slot(bool)
    def _on_edit_toggled(self, checked: bool) -> None:
        """Handle edit mode toggle."""
        self._set_edit_mode(checked)

    @Slot()
    def _on_add_device_clicked(self) -> None:
        """Handle add device button click."""
        if not self._edit_mode:
            return

        # Show device selection dialog
        self._show_device_picker()

    @Slot()
    def _on_add_beam_clicked(self) -> None:
        """Handle add beam segment button click."""
        if not self._edit_mode:
            return

        # Add a default beam segment
        segment = BeamPathSegment(
            start=(-1.0, 0.0, 0.0),
            end=(1.0, 0.0, 0.0),
            id=f"segment_{len(self._beam_path.get_segments())}",
        )
        self._beam_path.add_segment(segment)

        # Save
        if self._persistence:
            self._persistence.save_beam_path(self._beam_path.get_segments())

    def _show_device_picker(self) -> None:
        """Show dialog to pick devices to add to synoptic view."""
        # Get devices not yet in synoptic view
        available_devices = []
        for device_info in self._catalog.list_devices():
            device_id = str(device_info.id)
            if device_id not in self._device_items:
                available_devices.append(device_info)

        if not available_devices:
            QMessageBox.information(
                self,
                "No Devices",
                "All devices are already in the synoptic view.",
            )
            return

        # Create simple selection menu
        menu = QMenu(self)
        for device_info in available_devices[:20]:  # Limit to 20 items
            action = menu.addAction(device_info.name)
            action.setData(device_info)

        action = menu.exec_(self._add_device_button.mapToGlobal(
            self._add_device_button.rect().bottomLeft()
        ))

        if action:
            device_info = action.data()
            self._add_new_device(device_info)

    def _add_new_device(self, device_info: DeviceInfo) -> None:
        """Add a new device to the synoptic view.

        Args:
            device_info: Device to add.
        """
        device_id = str(device_info.id)

        # Create default synoptic data
        category = device_info.category.value if device_info.category else "other"
        synoptic_data = DeviceSynopticData.default_for_category(category)

        # Store in device metadata
        device_info.metadata["synoptic"] = synoptic_data.to_dict()

        # Create and add item with current view preset
        item = Device2DItem(
            device_id,
            device_info.name,
            synoptic_data,
            view_preset=self._view.get_view_preset(),
        )
        self._device_items[device_id] = item
        self._device_info_map[device_id] = device_info
        self._view.add_device_item(device_id, item)

        # Select the new device
        self._view.select_device(device_id)

        # Schedule save
        self._saver.schedule_save(device_info, synoptic_data)

        self._update_device_count()
        logger.info("Added device to synoptic view: {}", device_info.name)

    # === View Event Handlers ===

    @Slot(str)
    def _on_device_clicked(self, device_id: str) -> None:
        """Handle device click."""
        self.device_selected.emit(device_id)

        # Update property editor
        item = self._device_items.get(device_id)
        device_info = self._device_info_map.get(device_id)
        if item and device_info:
            self._property_editor.set_device(
                device_id,
                device_info.name,
                item.get_synoptic_data(),
            )
            self._update_gizmo()

    @Slot(str)
    def _on_device_double_clicked(self, device_id: str) -> None:
        """Handle device double-click (focus)."""
        self.device_focused.emit(device_id)

        # Post focus event to Device panel
        device_info = self._device_info_map.get(device_id)
        device_name = device_info.name if device_info else None

        registry = PanelRegistry.get_instance()
        device_panel = registry.get_singleton("lucid.panels.devices")

        if device_panel is not None:
            event = DeviceFocusEvent(device_id, device_name)
            QCoreApplication.postEvent(device_panel, event)
            logger.debug("Posted DeviceFocusEvent for device: {}", device_name or device_id)

    @Slot(list)
    def _on_selection_changed(self, device_ids: list[str]) -> None:
        """Handle selection change."""
        if not device_ids:
            self._property_editor.set_device(None, None, None)
            self._gizmo.hide()
        elif len(device_ids) == 1:
            device_id = device_ids[0]
            item = self._device_items.get(device_id)
            device_info = self._device_info_map.get(device_id)
            if item and device_info:
                self._property_editor.set_device(
                    device_id,
                    device_info.name,
                    item.get_synoptic_data(),
                )
                self._update_gizmo()
                # Post selection event to Device panel (unless this was from an event)
                if not self._selecting_from_event:
                    self._post_device_select_event(device_id)
        else:
            # Multi-selection
            self._property_editor.set_device(None, None, None)
            self._gizmo.hide()

    def _post_device_select_event(self, device_id: str) -> None:
        """Post a device select event to the Device panel.

        Args:
            device_id: ID of the selected device.
        """
        # Find the Device panel and post event to it
        registry = PanelRegistry.get_instance()
        device_panel = registry.get_singleton("lucid.panels.devices")

        if device_panel is not None:
            event = DeviceSelectEvent(device_id)
            QCoreApplication.postEvent(device_panel, event)
            logger.debug("Posted DeviceSelectEvent for device: {}", device_id)

    @Slot()
    def _on_view_changed(self) -> None:
        """Handle view change."""
        self._update_toolbar_state()

        # Save view state (debounced via preference manager)
        if self._persistence:
            state = self._view.get_view_state()
            self._persistence.save_view_state(state)

    @Slot(str, object)
    def _on_property_data_changed(
        self,
        device_id: str,
        data: DeviceSynopticData,
    ) -> None:
        """Handle property editor data change."""
        item = self._device_items.get(device_id)
        device_info = self._device_info_map.get(device_id)

        if item and device_info:
            # Update 2D item
            item.set_synoptic_data(data)

            # Update gizmo position
            self._update_gizmo()

            # Schedule save
            self._saver.schedule_save(device_info, data)

    @Slot(str, tuple)
    def _on_device_moved(self, device_id: str, new_position: tuple) -> None:
        """Handle device drag movement from view.

        Args:
            device_id: Device that was moved.
            new_position: New 3D position (x, y, z).
        """
        item = self._device_items.get(device_id)
        device_info = self._device_info_map.get(device_id)

        if item and device_info:
            # Get current synoptic data and update position
            data = item.get_synoptic_data()
            data.position = new_position

            # Update property editor if this device is selected
            if device_id in self._view.get_selected_device_ids():
                self._property_editor.set_device(
                    device_id,
                    device_info.name,
                    data,
                )

            # Update gizmo position
            self._update_gizmo()

            # Schedule save
            self._saver.schedule_save(device_info, data)

    # === Catalog Event Handlers ===

    @Slot(object)
    def _on_device_added(self, device_info: DeviceInfo) -> None:
        """Handle device added to catalog."""
        if self._has_synoptic_config(device_info):
            self._add_device_to_view(device_info)
            self._update_device_count()

    @Slot(object)
    def _on_device_removed(self, device_info: DeviceInfo) -> None:
        """Handle device removed from catalog."""
        device_id = str(device_info.id)
        if device_id in self._device_items:
            self._view.remove_device_item(device_id)
            del self._device_items[device_id]
            self._device_info_map.pop(device_id, None)
            self._update_device_count()

    # === Lifecycle ===

    def _on_activated(self) -> None:
        """Handle panel activation."""
        self._update_edit_permission()

    def _on_closing(self) -> None:
        """Handle panel closing."""
        # Flush any pending saves
        self._saver.flush()

        # Save final view state
        if self._persistence:
            state = self._view.get_view_state()
            self._persistence.save_view_state(state)

    # === Event Handling ===

    def event(self, event: QEvent) -> bool:
        """Handle custom events including DeviceFocusEvent.

        Args:
            event: The event to handle.

        Returns:
            True if event was handled, False otherwise.
        """
        if event.type() == DeviceFocusEvent.EventType:
            # Handle device focus request from another panel
            self._handle_device_focus_event(event)
            return True
        return super().event(event)

    def _handle_device_focus_event(self, event: DeviceFocusEvent) -> None:
        """Handle a device focus event by selecting and framing the device.

        Args:
            event: The device focus event.
        """
        device_id = event.device_id

        # Check if device exists in synoptic view
        if device_id not in self._device_items:
            logger.debug(
                "Device {} not in synoptic view, ignoring focus event",
                event.device_name or device_id,
            )
            return

        # Set flag to prevent posting event back to source panel
        self._selecting_from_event = True
        try:
            # Select and frame the device
            self._view.select_device(device_id)
            self._view._frame_selected()
            logger.debug("Focused device in synoptic: {}", event.device_name or device_id)
        finally:
            self._selecting_from_event = False

    # === Keyboard Shortcuts ===

    def keyPressEvent(self, event) -> None:
        """Handle keyboard shortcuts."""
        key = event.key()

        if key == Qt.Key.Key_Delete and self._edit_mode:
            self._delete_selected_devices()
        else:
            # Let view handle other shortcuts
            self._view.keyPressEvent(event)

    def _delete_selected_devices(self) -> None:
        """Remove selected devices from synoptic view."""
        selected = self._view.get_selected_device_ids()
        if not selected:
            return

        for device_id in selected:
            item = self._device_items.pop(device_id, None)
            if item:
                self._view.remove_device_item(device_id)

            # Remove synoptic data from device metadata
            device_info = self._device_info_map.pop(device_id, None)
            if device_info:
                device_info.metadata.pop("synoptic", None)
                # Persist the removal
                try:
                    self._catalog.update_device(device_info)
                except Exception as e:
                    logger.error("Failed to persist device update: {}", e)

        self._update_device_count()

    # === Introspection ===

    def _get_specific_introspection_data(self) -> dict[str, Any]:
        """Get synoptic-specific introspection data."""
        return {
            "edit_mode": self._edit_mode,
            "device_count": len(self._device_items),
            "beam_path_segments": len(self._beam_path.get_segments()) if self._beam_path else 0,
            "gizmo_visible": self._gizmo.is_visible() if self._gizmo else False,
            "view": self._view.get_introspection_data(),
            "selected_devices": self._view.get_selected_device_ids(),
        }

    def _get_available_actions(self) -> list[dict[str, Any]]:
        """Get available actions for MCP introspection."""
        actions = super()._get_available_actions()
        actions.extend([
            {
                "name": "set_view_preset",
                "description": "Set view projection preset",
                "method": "action_set_view_preset",
                "parameters": {"preset": "side|top|front"},
            },
            {
                "name": "toggle_edit_mode",
                "description": "Toggle edit mode (requires permission)",
                "method": "action_toggle_edit_mode",
            },
            {
                "name": "select_device",
                "description": "Select a device by ID",
                "method": "action_select_device",
                "parameters": {"device_id": "string"},
            },
            {
                "name": "focus_device",
                "description": "Focus view on a device",
                "method": "action_focus_device",
                "parameters": {"device_id": "string"},
            },
        ])
        return actions

    def action_set_view_preset(self, preset: str) -> bool:
        """Action: Set view preset."""
        try:
            view_preset = ViewPreset(preset.lower())
            # Map legacy presets to SIDE
            if view_preset in (ViewPreset.ORTHO3D, ViewPreset.PERSPECTIVE):
                view_preset = ViewPreset.SIDE
            self._view.apply_view_preset(view_preset)
            self._gizmo.set_view_preset(view_preset)
            self._beam_path.set_view_preset(view_preset)
            return True
        except ValueError:
            return False

    def action_toggle_edit_mode(self) -> bool:
        """Action: Toggle edit mode."""
        if not self._edit_action.isVisible():
            return False
        self._set_edit_mode(not self._edit_mode)
        return True

    def action_select_device(self, device_id: str) -> bool:
        """Action: Select a device."""
        if device_id in self._device_items:
            self._view.select_device(device_id)
            return True
        return False

    def action_focus_device(self, device_id: str) -> bool:
        """Action: Focus on a device."""
        if device_id in self._device_items:
            self._view.select_device(device_id)
            self._view._frame_selected()
            return True
        return False
