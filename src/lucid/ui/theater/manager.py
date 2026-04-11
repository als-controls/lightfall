"""TheaterManager — singleton coordinator for theater mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lucid.ui.theater.proxy import TheaterProxy


class TheaterManager:
    """Coordinates TheaterProxy instances and the TheaterOverlay."""

    def __init__(self) -> None:
        self._proxies: dict[int, TheaterProxy] = {}
        self._overlay = None

    def register(self, proxy: TheaterProxy) -> None:
        """Register a proxy and connect its expand signal."""
        widget_id = id(proxy.target_widget)
        self._proxies[widget_id] = proxy
        proxy.expand_requested.connect(lambda: self.activate(proxy))

    def unregister(self, proxy: TheaterProxy) -> None:
        """Unregister a proxy."""
        widget_id = id(proxy.target_widget)
        self._proxies.pop(widget_id, None)

    def activate(self, proxy: TheaterProxy) -> None:
        """Expand a proxy's widget onto the overlay (stub)."""

    def deactivate(self) -> None:
        """Collapse the currently expanded widget (stub)."""


theater_manager = TheaterManager()
