"""Helpers for reading Tsuchinoko's ``adaptive`` stream layout from Tiled.

Tsuchinoko's ``TiledPublisher`` writes the ``adaptive`` stream as a Bluesky
event stream: one descriptor whose ``data_keys`` carry
``source="tsuchinoko"`` (evaluation grids live in the descriptor's
``configuration.tsuchinoko.data``) and one event per GP iteration.  Every
data key is routed to a zarr array child indexed by event number, e.g.
``adaptive["hyperparameters"]`` has shape ``(n_iterations, n_hp)``.
"""

from __future__ import annotations

from typing import Any


def adaptive_data_keys(adaptive: Any) -> dict[str, Any]:
    """Return the adaptive stream's descriptor ``data_keys`` (``{}`` if absent)."""
    try:
        data_keys = adaptive.metadata.get("data_keys") or {}
    except Exception:
        return {}
    return data_keys if isinstance(data_keys, dict) else {}


def is_tsuchinoko_stream(adaptive: Any) -> bool:
    """True if *adaptive* was written by Tsuchinoko's TiledPublisher.

    Recognizes ``source="tsuchinoko"`` on any descriptor data key; the
    ``adaptive_engine`` metadata key is accepted as a legacy fallback for
    hand-written / older containers (the real writer never sets it).
    """
    if any(
        isinstance(dk, dict) and dk.get("source") == "tsuchinoko"
        for dk in adaptive_data_keys(adaptive).values()
    ):
        return True
    try:
        return adaptive.metadata.get("adaptive_engine") == "tsuchinoko"
    except Exception:
        return False


def adaptive_field_node(adaptive: Any, field: str) -> Any:
    """Return the array node for *field* in the adaptive stream.

    Tries the stream container directly (what the composite facade exposes
    for array children), then its plain ``.base`` container.  Raises
    ``KeyError`` if neither has the field.
    """
    try:
        return adaptive[field]
    except Exception:
        pass
    base = getattr(adaptive, "base", None)
    if base is not None:
        try:
            return base[field]
        except Exception:
            pass
    raise KeyError(field)
