"""Module path resolver for source code navigation.

Provides functions to resolve Python module names to their file paths
for opening in code editors.
"""

from __future__ import annotations

import importlib
import importlib.util
from functools import lru_cache
from pathlib import Path

from lucid.utils.logging import logger


@lru_cache(maxsize=256)
def resolve_module_path(module_name: str) -> str | None:
    """Resolve a Python module name to its file path.

    Takes a module name like 'lucid.ui.preferences' and returns the
    absolute file path to the corresponding Python file.

    Results are cached to improve performance for repeated lookups.

    Args:
        module_name: Fully qualified module name (e.g., "lucid.ui.preferences").

    Returns:
        Absolute file path as a string, or None if the module cannot be resolved.

    Example:
        >>> resolve_module_path("lucid.ui.preferences")
        "C:/Users/rp/PycharmProjects/ncs/ncs/src/ncs/ui/preferences/__init__.py"
    """
    try:
        # Try to find the module spec without importing
        spec = importlib.util.find_spec(module_name)
        if spec is not None and spec.origin is not None:
            # spec.origin contains the file path
            resolved = str(Path(spec.origin).resolve())
            logger.debug("Resolved {} to {}", module_name, resolved)
            return resolved

        # Fallback: try importing the module
        module = importlib.import_module(module_name)
        if hasattr(module, "__file__") and module.__file__ is not None:
            resolved = str(Path(module.__file__).resolve())
            logger.debug("Resolved {} to {} (via import)", module_name, resolved)
            return resolved

        logger.debug("Module {} has no __file__ attribute", module_name)
        return None

    except ImportError as e:
        logger.debug("Failed to resolve module {}: {}", module_name, e)
        return None
    except Exception as e:
        logger.warning("Unexpected error resolving module {}: {}", module_name, e)
        return None


def clear_cache() -> None:
    """Clear the module resolution cache.

    Call this if modules are being added/removed at runtime
    and you need fresh lookups.
    """
    resolve_module_path.cache_clear()
    logger.debug("Module resolution cache cleared")
