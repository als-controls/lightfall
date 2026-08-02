"""lightfall.agents — agent definition files, registry, skills store, assembly."""

from __future__ import annotations

from pathlib import Path


def builtin_agents_dir() -> Path:
    """Return the package directory containing built-in shipped agent files."""
    return Path(__file__).parent / "builtin"


def user_agents_dir() -> Path:
    """Return (and create) the user-scope agent definitions directory.

    Re-exported here for convenience; canonical definition lives in
    ``lightfall.agents.registry``.
    """
    from lightfall.agents.registry import user_agents_dir as _user_agents_dir

    return _user_agents_dir()


__all__ = ["builtin_agents_dir", "user_agents_dir"]
