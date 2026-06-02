"""Theater mode — generic widget expansion overlay.

Usage with install (existing widget in a layout)::

    from lucid.ui.theater import theater_manager

    plot = pg.PlotWidget()
    layout.addWidget(plot)
    theater_manager.install(plot)

Usage with direct proxy construction::

    from lucid.ui.theater import TheaterProxy

    proxy = TheaterProxy(my_image_view)
    layout.addWidget(proxy)
"""

from lucid.ui.theater.manager import TheaterManager, theater_manager
from lucid.ui.theater.overlay import TheaterOverlay
from lucid.ui.theater.proxy import TheaterProxy

__all__ = [
    "TheaterManager",
    "TheaterOverlay",
    "TheaterProxy",
    "theater_manager",
]
