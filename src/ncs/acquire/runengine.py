"""Qt-integrated Bluesky RunEngine for NCS.

Deprecated: This module is maintained for backward compatibility.
Use ncs.acquire.engine instead.

Example (old):
    from ncs.acquire.runengine import QRunEngine, get_run_engine
    re = get_run_engine()

Example (new):
    from ncs.acquire import get_engine, BlueskyEngine
    engine = get_engine()
"""

from __future__ import annotations

from ncs.acquire.engine import BlueskyEngine, get_engine

__all__ = ["QRunEngine", "get_run_engine"]

# Backward compatibility aliases
QRunEngine = BlueskyEngine


def get_run_engine(**kwargs):
    """Get the global QRunEngine instance.

    Deprecated: Use get_engine() instead.

    Args:
        **kwargs: Arguments passed to BlueskyEngine on first initialization.

    Returns:
        The global BlueskyEngine instance.
    """
    return get_engine(**kwargs)
