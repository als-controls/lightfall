"""TheaterManager — singleton coordinator for theater mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from lucid.ui.theater.overlay import TheaterOverlay
    from lucid.ui.theater.proxy import TheaterProxy


class TheaterManager:
    """Coordinates TheaterProxy instances and the TheaterOverlay."""

    def __init__(self) -> None:
        self._proxies: dict[int, TheaterProxy] = {}
        self._overlay: TheaterOverlay | None = None

    def register(self, proxy: TheaterProxy) -> None:
        """Register a proxy and connect its expand signal."""
        widget_id = id(proxy.target_widget)
        self._proxies[widget_id] = proxy
        proxy.expand_requested.connect(lambda: self.activate(proxy))

    def unregister(self, proxy: TheaterProxy) -> None:
        """Unregister a proxy."""
        widget_id = id(proxy.target_widget)
        self._proxies.pop(widget_id, None)
        try:
            proxy.expand_requested.disconnect()
        except RuntimeError:
            pass

    def install(self, widget: QWidget) -> TheaterProxy:
        """Wrap an already-laid-out widget in a TheaterProxy.

        Finds the widget's parent layout and replaces the widget
        at the same index with a new TheaterProxy.
        """
        from lucid.ui.theater.proxy import TheaterProxy

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

    def uninstall(self, widget: QWidget) -> None:
        """Remove theater mode from a widget, restoring it to its layout."""
        widget_id = id(widget)
        proxy = self._proxies.get(widget_id)
        if proxy is None:
            return

        # Deactivate if currently in theater mode
        if self._overlay is not None and self._overlay._active_proxy is proxy:
            self._overlay._finish_deactivate()

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
            from lucid.ui.theater.overlay import TheaterOverlay

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
