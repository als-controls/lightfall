"""People-facing names for content sources and scopes (no Qt, no scopes.client import)."""
from __future__ import annotations

_FIXED = {
    "core": "Built-in",
    "builtin": "Built-in",
    "package": "Package",
    "plugin": "Plugin",
    "local": "Mine",
    "facility": "Facility",
}


def describe_source(source: str) -> str:
    """"branch:7.0.1.1" -> "Branch 7.0.1.1"; "local" -> "Mine"; unknown values pass through."""
    if source in _FIXED:
        return _FIXED[source]
    kind, _, rest = source.partition(":")
    if kind == "beamline" and rest:
        return f"Beamline {rest}"
    if kind == "branch" and rest:
        return f"Branch {rest}"
    return source or "this scope"
