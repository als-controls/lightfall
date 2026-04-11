"""Converter registry for the exporter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lucid.exporter.converters.base import Converter

CONVERTERS: dict[str, type[Converter]] = {}


def register_converter(cls: type[Converter]) -> type[Converter]:
    """Register a converter class by its name attribute."""
    CONVERTERS[cls.name] = cls
    return cls


def get_converter(name: str) -> type[Converter]:
    """Get a converter class by name. Raises KeyError if not found."""
    return CONVERTERS[name]


# Import converters to trigger registration (nxsas added in Task 2)
from lucid.exporter.converters.noop import NoOpConverter  # noqa: E402, F401
