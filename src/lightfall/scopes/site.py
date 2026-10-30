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


def set_site_bind(bind: str) -> None:
    """Bind this machine to ``bind`` (``"facility"`` unbinds) in the user config file.

    Takes effect on the next start: the scope chain, identity defaults and the device
    rows all come from it.
    """
    from lightfall.config import ConfigManager
    from lightfall.core import LFApplication

    cm = LFApplication.get_instance().services.get(ConfigManager)
    cm.set("site.bind", None if bind == "facility" else bind, persist=True)
    logger.info("site.bind set to {}", bind)


def binding_names() -> list[str]:
    """This console's beamline names, most specific first: ``["7.0.1.1", "7.0.1"]`` for
    ``branch:7.0.1.1`` (its beamline comes from the active scope chain), ``[]`` unbound."""
    bind = get_site_bind()
    if bind == "facility":
        return []
    names = [bind.partition(":")[2]]
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        chain = PreferencesManager.get_instance().scope_info.chain
    except Exception:  # no Qt app / manager yet
        chain = ()
    names += [s.partition(":")[2] for s in chain
              if s.startswith("beamline:") and s.partition(":")[2] not in names]
    return names


def default_cache_dir() -> Path:
    return _user_config_dir("ncs") / "scope-cache"
