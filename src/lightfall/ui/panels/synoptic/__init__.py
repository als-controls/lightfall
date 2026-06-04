"""3D Synoptic view panel for beamline hardware visualization.

This package provides:
- SynopticPanel: Panel for 3D beamline visualization
- SynopticView: GLViewWidget-based 3D view
- Device and beam path rendering items
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lightfall.ui.panels.synoptic.panel import SynopticPanel

__all__ = [
    "SynopticPanel",
]


def __getattr__(name: str):
    """Lazy import for SynopticPanel."""
    if name == "SynopticPanel":
        from lightfall.ui.panels.synoptic.panel import SynopticPanel

        return SynopticPanel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
