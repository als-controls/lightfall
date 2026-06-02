"""LUCID - Lightsource Unified Control Interface Dashboard for the ALS facility."""

try:
    from lightfall._version import __version__
except ImportError:
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
