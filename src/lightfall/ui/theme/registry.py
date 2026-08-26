"""Compatibility shim: the theme registry now lives in lightfall-utils."""

from lightfall_utils.theming.registry import ThemeRegistry  # noqa: F401

__all__ = ["ThemeRegistry"]
