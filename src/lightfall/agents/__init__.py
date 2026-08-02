"""lightfall.agents — agent definition files, registry, skills store, assembly."""

from __future__ import annotations

from pathlib import Path


def builtin_agents_dir() -> Path:
    """Return the package directory containing built-in shipped agent files."""
    return Path(__file__).parent / "builtin"


__all__ = ["builtin_agents_dir"]
