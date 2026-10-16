"""Client side of scoped settings (facility / beamline / branch / user).

Everything here may import ``lightfall_settings``, which is installed separately
(it is not on PyPI yet). Callers outside this package check ``scopes_available()``
before importing the submodules that need it.
"""
from __future__ import annotations


def scopes_available() -> bool:
    try:
        import lightfall_settings  # noqa: F401
    except ImportError:
        return False
    return True
