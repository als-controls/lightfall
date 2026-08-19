"""Reusable Bluesky plan stubs shared across built-in and user plans.

These helpers exist so plans can assert the hardware state they require at
their start instead of relying on a device's staged defaults. In particular,
area-detector image mode is no longer forced at stage time (see the PIMTE3
device class), so every plan is responsible for selecting the image mode it
needs:

- Step / list / grid scans that read one (or a few) frames per point want
  "Multiple" mode -- call :func:`set_multiple_mode` at the top of the plan.
- Free-running acquisition (``continuous_acquire``) sets "Continuous" itself.

The helpers are intentionally permissive: any detector lacking a
``cam.image_mode`` signal (scalars, simulated detectors, non-area-detectors)
is skipped, so they are safe to call with a heterogeneous detector list.
"""

from __future__ import annotations

from typing import Any, Generator

import bluesky.plan_stubs as bps

# AreaDetector ImageMode mbbo enum indices: ('Single', 'Multiple', 'Continuous').
IMAGE_MODE_SINGLE = 0
IMAGE_MODE_MULTIPLE = 1
IMAGE_MODE_CONTINUOUS = 2


def _iter_detectors(detectors: Any) -> list[Any]:
    """Normalize a single detector or a list/tuple of them to a list."""
    if detectors is None:
        return []
    if isinstance(detectors, (list, tuple, set)):
        return list(detectors)
    return [detectors]


def set_image_mode(detectors: Any, mode: int) -> Generator[Any, Any, None]:
    """Set ``cam.image_mode`` on every detector that supports it.

    Args:
        detectors: A single detector or a list/tuple of detectors. Entries
            without a ``cam.image_mode`` signal are silently skipped.
        mode: Target image-mode enum index (see ``IMAGE_MODE_*`` constants).

    Yields:
        Bluesky plan messages (one ``mv`` per supporting detector).
    """
    targets = [
        image_mode
        for det in _iter_detectors(detectors)
        if (image_mode := getattr(getattr(det, "cam", None), "image_mode", None))
        is not None
    ]
    if targets:
        # Flatten alternating (signal, value) pairs for a single grouped move.
        args: list[Any] = []
        for image_mode in targets:
            args.extend((image_mode, mode))
        yield from bps.mv(*args)


def set_multiple_mode(detectors: Any) -> Generator[Any, Any, None]:
    """Put any area detectors into "Multiple" image mode.

    Convenience wrapper around :func:`set_image_mode`. Call this at the start
    of a step/list/grid-scan plan so the camera is guaranteed not to be left
    free-running in "Continuous" mode (which hangs ``SingleTrigger``).
    """
    yield from set_image_mode(detectors, IMAGE_MODE_MULTIPLE)
