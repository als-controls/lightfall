"""Identity preferences derived from the machine's site.bind (spec section 5).

A beamline console bound to ``branch:7.0.1.1`` gets ``tiled_beamline = "7.0.1.1"`` and
``ipc_topic_prefix = "als.7011"`` without per-machine YAML. Any value set in a scope
or in the user's YAML wins; these are defaults only.
"""
from __future__ import annotations

# current_beamline (synoptic layout key) is deliberately not derived: saved layouts are
# keyed by it, so changing its default would hide them (phase-4c review I-5).
DERIVED_KEYS: frozenset[str] = frozenset({"tiled_beamline", "ipc_topic_prefix"})


def beamline_of(bind: str | None) -> str | None:
    """Beamline name for a bind. Branches follow the 7.0.1 -> 7.0.1.1 naming convention."""
    if not bind:
        return None
    kind, _, name = bind.partition(":")
    if kind == "beamline":
        return name or None
    if kind == "branch" and "." in name:
        return name.rsplit(".", 1)[0]
    return None


def derived_default(key: str, bind: str | None, facility: str) -> str | None:
    if key not in DERIVED_KEYS or not bind or bind == "facility":
        return None
    _kind, _, name = bind.partition(":")
    if key == "tiled_beamline":
        return name or None  # ALS ids: 4.0.2, 7.0.1.1 (branches are beamlines to ALSHub)
    if key == "ipc_topic_prefix" and name and facility:
        return f"{facility}.{name.replace('.', '')}"
    return None
