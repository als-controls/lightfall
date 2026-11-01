"""Whether a catalog item (plugin, tool, monitor feed) is switched on or off by settings.

Importable before the Qt app or the preferences exist: PreferencesManager is imported
lazily, and nothing here raises. Runtime code passes the code default (``enabled_by_default``)
and gets the resolved ``{id: bool}`` map's answer when it has one.
"""
from __future__ import annotations


def enabled_overrides(key: str) -> dict[str, bool]:
    """The resolved ``{item id: bool}`` map of an item-merged key (non-bool entries dropped)."""
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        items = PreferencesManager.get_instance().resolved_items(key)
    except Exception:  # noqa: BLE001 - early startup, tests: no preferences, no overrides
        return {}
    return {str(k): v for k, v in items.items() if isinstance(v, bool)}


def item_enabled(key: str, item_id: str, default: bool) -> bool:
    """The resolved map's answer for ``item_id``, else ``default``."""
    return enabled_overrides(key).get(item_id, default)
