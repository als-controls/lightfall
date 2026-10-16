"""Machine binding and cache location for scoped settings."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lightfall.utils.logging import logger
from lightfall_utils.config.manager import _user_config_dir


def _site_config() -> Any:
    from lightfall.config import ConfigManager
    from lightfall.core import LFApplication

    return LFApplication.get_instance().services.get(ConfigManager).model.site


def get_site_bind() -> str:
    """The scope this machine binds to, or ``"facility"`` when unbound/unknown."""
    try:
        return _site_config().bind or "facility"
    except Exception as e:
        logger.debug("site.bind unavailable ({}); using facility", e)
        return "facility"


def default_cache_dir() -> Path:
    return _user_config_dir("ncs") / "scope-cache"
