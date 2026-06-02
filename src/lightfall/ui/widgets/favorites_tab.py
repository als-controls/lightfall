"""Favorites tab widget for the Devices panel.

Displays a vertical list of CompactMotorWidgets for favorited devices.

Favorites are identified by device NAME (DeviceInfo.name), not the
runtime UUID, so they survive catalog rebuilds across sessions. Widgets
for favorites whose device is not yet in the catalog stay pending until
DeviceCatalog.device_added arrives for that name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.widgets.compact_motor import CompactMotorWidget
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.devices.catalog import DeviceCatalog


class FavoritesTab(QWidget):
    """Tab showing compact control widgets for favorited devices.

    All `device_id` parameters and emissions in this class are device
    NAMES (stable across sessions). UUIDs are not used here.

    Signals:
        open_controller_requested: name — user wants full controller tab.
        favorite_removed: name — a favorite was removed.
        favorites_changed: list[str] — favorites list (names) changed.
    """

    open_controller_requested = Signal(str)
    favorite_removed = Signal(str)
    favorites_changed = Signal(list)

    def __init__(self, catalog: DeviceCatalog, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._favorite_ids: list[str] = []
        self._widgets: dict[str, CompactMotorWidget] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer_layout.addWidget(self._scroll)

        self._container = QWidget()
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(2)

        self._placeholder = QLabel("Right-click a device in the All tab to add favorites")
        self._placeholder.setStyleSheet("color: #888; font-style: italic; padding: 20px;")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._list_layout.addWidget(self._placeholder)

        self._list_layout.addStretch()
        self._scroll.setWidget(self._container)

    def is_favorite(self, device_id: str) -> bool:
        return device_id in self._favorite_ids

    def get_favorite_ids(self) -> list[str]:
        return list(self._favorite_ids)

    def set_favorites(self, device_ids: list[str]) -> None:
        """Set the full favorites list (for loading from prefs).

        `device_ids` is a list of device NAMES. Names whose device is not
        yet in the catalog are kept in `_favorite_ids` but have no widget
        yet — `on_device_added` creates the widget once the device shows
        up.

        Does NOT emit favorites_changed to avoid re-saving what was just
        loaded. Use add_favorite/remove_favorite for user-initiated changes.

        Skip the teardown/rebuild when the list is unchanged — subscribe()
        echoes our own writes back, and a rebuild would briefly clear the
        visible widgets.
        """
        if device_ids == self._favorite_ids:
            return
        for did in list(self._favorite_ids):
            self._remove_widget(did)
        self._favorite_ids.clear()
        for did in device_ids:
            self._favorite_ids.append(did)
            self._try_create_widget(did)
        self._update_placeholder()

    def add_favorite(self, device_id: str) -> None:
        """Add a favorite by device NAME."""
        if device_id in self._favorite_ids:
            return
        self._favorite_ids.append(device_id)
        self._try_create_widget(device_id)
        self._update_placeholder()
        self.favorites_changed.emit(self.get_favorite_ids())

    def remove_favorite(self, device_id: str) -> None:
        """Remove a favorite by device NAME."""
        if device_id not in self._favorite_ids:
            return
        self._remove_widget(device_id)
        self._update_placeholder()
        self.favorites_changed.emit(self.get_favorite_ids())
        self.favorite_removed.emit(device_id)

    def _try_create_widget(self, name: str) -> None:
        """Render a CompactMotorWidget for `name` if its device is in the
        catalog. No-op if already rendered or the device is missing —
        deferred favorites are picked up when DeviceCatalog.device_added
        eventually fires for them."""
        from lightfall.devices.model import DeviceCategory

        if name in self._widgets:
            return
        info = self._catalog.get_device_by_name(name)
        if info is None:
            logger.debug("Favorite {} not in catalog yet — deferring", name)
            return
        if info.category != DeviceCategory.MOTOR:
            logger.debug("Skipping non-motor favorite: {} ({})", info.name, info.category)
            return
        ophyd_obj = getattr(info, "_ophyd_device", None)
        widget = CompactMotorWidget(
            device_info=info, ophyd_obj=ophyd_obj, parent=self._container
        )
        widget.open_controller_requested.connect(self.open_controller_requested)
        widget.remove_favorite_requested.connect(self.remove_favorite)
        insert_idx = self._list_layout.count() - 1  # before stretch
        self._list_layout.insertWidget(insert_idx, widget)
        self._widgets[name] = widget

    def _remove_widget(self, device_id: str) -> None:
        widget = self._widgets.pop(device_id, None)
        if widget is not None:
            self._list_layout.removeWidget(widget)
            widget.close()
            widget.deleteLater()
        if device_id in self._favorite_ids:
            self._favorite_ids.remove(device_id)

    def _update_placeholder(self) -> None:
        # Show the hint only when no favorites are recorded at all (so a
        # user with a pending-but-unloaded favorite isn't told to add one).
        self._placeholder.setVisible(len(self._favorite_ids) == 0)

    @Slot(object)
    def on_device_added(self, info) -> None:
        """Pick up a deferred favorite when its device arrives."""
        if info is None:
            return
        if info.name in self._favorite_ids and info.name not in self._widgets:
            self._try_create_widget(info.name)
            self._update_placeholder()

    @Slot(str)
    def on_device_connected(self, device_id_str: str) -> None:
        """Catalog emits the device's UUID string on connect — translate
        to name so we can find the widget keyed by name."""
        info = self._catalog.get_device(device_id_str)
        if info is None:
            return
        widget = self._widgets.get(info.name)
        if widget is None:
            return
        ophyd_obj = getattr(info, "_ophyd_device", None)
        if ophyd_obj is not None:
            widget.set_motor(ophyd_obj)

    def closeEvent(self, event) -> None:
        for did in list(self._widgets.keys()):
            self._remove_widget(did)
        super().closeEvent(event)
