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

Image modes are given by meaning (``IMAGE_MODE_*``) and mapped onto each
camera's own ImageMode enum via :func:`image_mode_moves`. Driver enums differ:
generic AreaDetector is ('Single', 'Multiple', 'Continuous'), while ADAndor3 is
('Fixed', 'Continuous'), so a raw index of 1 means "Continuous" there.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import bluesky.plan_stubs as bps

# Requested image modes. The values are the generic AreaDetector ImageMode
# indices ('Single', 'Multiple', 'Continuous'); they are only written as-is to
# signals that expose no enum strings (simulated/soft cams). For EPICS cams,
# use image_mode_moves() to map them onto the driver's own enum.
IMAGE_MODE_SINGLE = 0
IMAGE_MODE_MULTIPLE = 1
IMAGE_MODE_CONTINUOUS = 2

_MODE_NAMES = {
    IMAGE_MODE_SINGLE: "Single",
    IMAGE_MODE_MULTIPLE: "Multiple",
    IMAGE_MODE_CONTINUOUS: "Continuous",
}
# Drivers with no Single/Multiple (e.g. ADAndor3) take N frames in "Fixed" mode.
_FIXED_MODE = "Fixed"


def _enum_strs(signal: Any) -> tuple[str, ...] | None:
    """The signal's enum strings, connecting it first if needed; None if not an enum."""
    try:
        strs = signal.enum_strs
    except (AttributeError, KeyError):
        return None
    if strs is None and hasattr(signal, "wait_for_connection"):
        try:
            signal.wait_for_connection()
            strs = signal.enum_strs
        except Exception:  # noqa: BLE001 - unconnected / non-enum signals alike
            return None
    return tuple(strs) if strs else None


def image_mode_moves(cam: Any, mode: int | str,
                     num_images: int | None = None) -> list[Any]:
    """``(signal, value, ...)`` pairs that put ``cam`` into ``mode``.

    ``mode`` is an ``IMAGE_MODE_*`` constant or its name ("Single",
    "Multiple", "Continuous"). The value written is the matching index of
    ``cam.image_mode``'s own enum. If the driver has no "Single"/"Multiple"
    but has "Fixed" (ADAndor3), Fixed is used and ``num_images`` is set too
    (1 for Single when not given). Signals without enum strings get the
    generic index. Raises ValueError if the camera has no equivalent mode.
    """
    name = _MODE_NAMES.get(mode, mode) if isinstance(mode, int) else mode
    if name not in _MODE_NAMES.values():
        raise ValueError(f"Unknown image mode {mode!r}")
    signal = cam.image_mode
    enum = _enum_strs(signal)
    if enum is None:
        index = next(i for i, n in _MODE_NAMES.items() if n == name)
        moves: list[Any] = [signal, index]
    elif name in enum:
        moves = [signal, enum.index(name)]
    elif name != "Continuous" and _FIXED_MODE in enum:
        moves = [signal, enum.index(_FIXED_MODE)]
        if num_images is None and name == "Single":
            num_images = 1
    else:
        raise ValueError(
            f"{getattr(signal, 'name', signal)} has no {name!r} image mode "
            f"(available: {list(enum)})"
        )
    if num_images is not None and hasattr(cam, "num_images"):
        moves.extend((cam.num_images, num_images))
    return moves


def _iter_detectors(detectors: Any) -> list[Any]:
    """Normalize a single detector or a list/tuple of them to a list."""
    if detectors is None:
        return []
    if isinstance(detectors, (list, tuple, set)):
        return list(detectors)
    return [detectors]


def set_image_mode(detectors: Any, mode: int | str) -> Generator[Any, Any, None]:
    """Set ``cam.image_mode`` on every detector that supports it.

    Args:
        detectors: A single detector or a list/tuple of detectors. Entries
            without a ``cam.image_mode`` signal are silently skipped.
        mode: Requested mode, an ``IMAGE_MODE_*`` constant or its name. It is
            mapped onto each camera's own enum (see :func:`image_mode_moves`).

    Yields:
        Bluesky plan messages (one grouped ``mv``).
    """
    args: list[Any] = []
    for det in _iter_detectors(detectors):
        cam = getattr(det, "cam", None)
        if getattr(cam, "image_mode", None) is not None:
            args.extend(image_mode_moves(cam, mode))
    if args:
        yield from bps.mv(*args)


def set_multiple_mode(detectors: Any) -> Generator[Any, Any, None]:
    """Put any area detectors into "Multiple" image mode.

    Convenience wrapper around :func:`set_image_mode`. Call this at the start
    of a step/list/grid-scan plan so the camera is guaranteed not to be left
    free-running in "Continuous" mode (which hangs ``SingleTrigger``).
    """
    yield from set_image_mode(detectors, IMAGE_MODE_MULTIPLE)
