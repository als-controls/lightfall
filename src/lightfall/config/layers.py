"""Compatibility shim: layered config machinery now lives in lightfall-utils."""

from lightfall_utils.config.layers import ConfigLayer, ConfigPriority, LayeredConfig  # noqa: F401

__all__ = ["ConfigLayer", "ConfigPriority", "LayeredConfig"]
