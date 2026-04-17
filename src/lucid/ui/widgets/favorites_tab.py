"""Favorites tab widget for the Devices panel.

Displays a vertical list of CompactMotorWidgets for favorited devices.
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

from lucid.ui.widgets.compact_motor import CompactMotorWidget
from lucid.utils.logging import logger

if TYPE_CHECKING:
    from lucid.devices.catalog import DeviceCatalog


class FavoritesTab(QWidget):
    """Tab showing compact control widgets for favorited devices.

    Signals:
        open_controller_requested: device_id — user wants full controller tab.
        favorite_removed: device_id — a favorite was removed.
        favorites_changed: list[str] — favorites list changed (for persistence).
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

        Does NOT emit favorites_changed to avoid re-saving what was just
        loaded. Use add_favorite/remove_favorite for user-initiated changes.
        """
        for did in list(self._favorite_ids):
            self._remove_widget(did)
        self._favorite_ids.clear()
        for did in device_ids:
            self._add_widget(did)
        self._update_placeholder()

    def add_favorite(self, device_id: str) -> None:
        if device_id in self._favorite_ids:
            return
        self._add_widget(device_id)
        self._update_placeholder()
        self.favorites_changed.emit(self.get_favorite_ids())

    def remove_favorite(self, device_id: str) -> None:
        if device_id not in self._favorite_ids:
            return
        self._remove_widget(device_id)
        self._update_placeholder()
        self.favorites_changed.emit(self.get_favorite_ids())
        self.favorite_removed.emit(device_id)

    def _add_widget(self, device_id: str) -> None:
        from lucid.devices.model import DeviceCategory

        info = self._catalog.get_device(device_id)
        if info is None:
            logger.warning("Favorite device {} not found in catalog, skipping", device_id)
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
        self._favorite_ids.append(device_id)
        self._widgets[device_id] = widget

    def _remove_widget(self, device_id: str) -> None:
        widget = self._widgets.pop(device_id, None)
        if widget is not None:
            self._list_layout.removeWidget(widget)
            widget.close()
            widget.deleteLater()
        if device_id in self._favorite_ids:
            self._favorite_ids.remove(device_id)

    def _update_placeholder(self) -> None:
        self._placeholder.setVisible(len(self._widgets) == 0)

    @Slot(str)
    def on_device_connected(self, device_id_str: str) -> None:
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
        for did in list(self._widgets.keys()):
            self._remove_widget(did)
        super().closeEvent(event)
