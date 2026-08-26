"""Compatibility shim: the Qt PV bridge now lives in lightfall-utils."""

from lightfall_utils.ca.pv import PV  # noqa: F401

__all__ = ["PV"]
