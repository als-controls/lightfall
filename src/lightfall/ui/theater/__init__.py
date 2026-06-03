"""Theater mode — generic widget expansion overlay.

Usage with install (existing widget in a layout)::

    from lightfall.ui.theater import theater_manager

    plot = pg.PlotWidget()
    layout.addWidget(plot)
    theater_manager.install(plot)

Usage with direct proxy construction::

    from lightfall.ui.theater import TheaterProxy

    proxy = TheaterProxy(my_image_view)
    layout.addWidget(proxy)
"""

from lightfall.ui.theater.manager import TheaterManager, theater_manager
from lightfall.ui.theater.overlay import TheaterOverlay
from lightfall.ui.theater.proxy import TheaterProxy

__all__ = [
    "TheaterManager",
    "TheaterOverlay",
    "TheaterProxy",
    "theater_manager",
]
