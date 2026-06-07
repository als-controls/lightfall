"""TheaterManager — singleton coordinator for theater mode."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from lightfall.ui.theater.overlay import TheaterOverlay
    from lightfall.ui.theater.proxy import TheaterProxy


class TheaterManager:
    """Coordinates TheaterProxy instances and the TheaterOverlay."""

    def __init__(self) -> None:
        self._proxies: dict[int, TheaterProxy] = {}
        self._slots: dict[int, partial] = {}
        self._overlay: TheaterOverlay | None = None

    def register(self, proxy: TheaterProxy) -> None:
        """Register a proxy and connect its expand signal."""
        widget_id = id(proxy.target_widget)
        self._proxies[widget_id] = proxy
        slot = partial(self.activate, proxy)
        self._slots[widget_id] = slot
        proxy.expand_requested.connect(slot)
        proxy.destroyed.connect(lambda: self._on_proxy_destroyed(widget_id))

    def unregister(self, proxy: TheaterProxy) -> None:
        """Unregister a proxy."""
        widget_id = id(proxy.target_widget)
        self._proxies.pop(widget_id, None)
        slot = self._slots.pop(widget_id, None)
        if slot is not None:
            try:
                proxy.expand_requested.disconnect(slot)
            except RuntimeError:
                pass

    def _on_proxy_destroyed(self, widget_id: int) -> None:
        """Auto-cleanup when a proxy is destroyed without unregister."""
        self._proxies.pop(widget_id, None)
        self._slots.pop(widget_id, None)

    def install(self, widget: QWidget) -> TheaterProxy:
        """Wrap an already-laid-out widget in a TheaterProxy.

        Finds the widget's parent layout and replaces the widget
        at the same index with a new TheaterProxy.
        """
        from lightfall.ui.theater.proxy import TheaterProxy

        parent = widget.parentWidget()
        if parent is None:
            msg = "Cannot install theater mode on a widget without a parent"
            raise ValueError(msg)
        layout = parent.layout()
        if layout is None:
            msg = "Cannot install theater mode: parent widget has no layout"
            raise ValueError(msg)

        index = layout.indexOf(widget)
        if index < 0:
            msg = "Widget not found in parent's layout"
            raise ValueError(msg)

        proxy = TheaterProxy(widget)
        layout.insertWidget(index, proxy)
        return proxy

    def release(self, proxy: TheaterProxy) -> None:
        """Synchronously collapse the overlay if this proxy is active.

        Call before hiding or destroying a proxy's host so the overlay
        never holds a stale reference (no animation — immediate).
        """
        if self._overlay is not None and self._overlay._active_proxy is proxy:
            self._overlay._finish_deactivate()

    def uninstall(self, widget: QWidget) -> None:
        """Remove theater mode from a widget, restoring it to its layout."""
        widget_id = id(widget)
        proxy = self._proxies.get(widget_id)
        if proxy is None:
            return

        # Deactivate if currently in theater mode
        self.release(proxy)

        # Restore widget to layout
        parent = proxy.parentWidget()
        layout = parent.layout() if parent else None

        if layout is not None:
            index = layout.indexOf(proxy)
            target = proxy.take_widget()
            layout.removeWidget(proxy)
            if index >= 0:
                layout.insertWidget(index, target)
            else:
                layout.addWidget(target)

        self.unregister(proxy)
        proxy.deleteLater()

    def activate(self, proxy: TheaterProxy) -> None:
        """Expand a proxy's widget onto the overlay."""
        if self._overlay is None:
            from lightfall.ui.theater.overlay import TheaterOverlay

            parent = proxy.window()
            self._overlay = TheaterOverlay(parent)
        self._overlay.activate(proxy)

    def deactivate(self) -> None:
        """Collapse the currently expanded widget."""
        if self._overlay is not None:
            self._overlay.deactivate()

    @property
    def is_active(self) -> bool:
        """Whether a widget is currently in theater mode."""
        return (
            self._overlay is not None
            and self._overlay._active_proxy is not None
        )


theater_manager = TheaterManager()
