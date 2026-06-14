"""Parse independent-axis (scan) geometry from a tiled BlueskyRun.

Extracts the motor field names and, for rectilinear scans, the grid shape, so
visualizations can lay out per-point values. Mirrors the detection inlined in
heatmap.py / scatter.py but as a standalone, tested helper.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScanGeometry:
    """Independent-axis layout of a scan.

    Attributes:
        motors: Motor field names, in scan-dimension order.
        n_dims: Number of scan dimensions (len of hints.dimensions).
        is_rectilinear: True when a positive-integer grid shape is recorded.
        grid_shape: The rectilinear grid shape, or ``()`` if non-rectilinear.
    """

    motors: list[str] = field(default_factory=list)
    n_dims: int = 0
    is_rectilinear: bool = False
    grid_shape: tuple[int, ...] = ()


def parse_scan_geometry(run: Any) -> ScanGeometry:
    """Build a :class:`ScanGeometry` from a run's start metadata."""
    try:
        start = run.metadata.get("start", {}) or {}
    except Exception:
        return ScanGeometry()

    dims = start.get("hints", {}).get("dimensions", []) or []
    motors: list[str] = []
    for entry in dims:
        try:
            fields, _stream = entry
            motors.extend(fields)
        except Exception:
            continue

    shape_raw = start.get("shape", []) or []
    try:
        grid = tuple(int(s) for s in shape_raw)
    except Exception:
        grid = ()
    is_rect = len(grid) >= 1 and all(s > 0 for s in grid)

    return ScanGeometry(
        motors=motors,
        n_dims=len(dims),
        is_rectilinear=is_rect,
        grid_shape=grid if is_rect else (),
    )
