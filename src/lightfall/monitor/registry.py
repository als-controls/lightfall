"""Singleton registry of MonitorPlugins, and what the monitor settings resolve to.

Which monitors and feeds run, and how, comes from four scoped, item-merged settings
(user, branch, beamline, facility, plus this console's own layer — LOCAL_LAYER_KEYS):

* ``monitors.enabled``        ``{monitor plugin name: bool}``; an unmentioned plugin uses
  its ``enabled_by_default``.
* ``monitor_feeds.enabled``   ``{feed name: bool}``; an unmentioned feed runs.
* ``monitor_feeds.interval``  ``{feed name: seconds}`` (int > 0); else ``default_interval_s``.
* ``monitor_feeds.severity``  ``{feed name: "info" | "warn" | "critical"}``: the lowest
  severity the advisor is told about; else "info".

Readers resolve the maps on every call (``enabled_plugins``, ``disabled_feed_names``,
``feed_interval_overrides``, ``advisor_floor``), so a change applies at the next scheduler
tick or observation. Stored values of the wrong type or out of range are ignored.

The old keys (``disabled_monitor_plugins``, ``forced_enabled_monitor_plugins``,
``disabled_monitor_feeds``, ``monitor_feed_intervals``, ``monitor_feed_advisor_severity``)
migrate once (``migrate_monitor_prefs``, run at startup and on each online scope fetch by
``start_monitor_migration``, with the same targets and gate as ``plugins.enabled``) and are
not read afterwards.
"""

from __future__ import annotations

import threading
import weakref
from typing import TYPE_CHECKING, Any

from lightfall.scopes.enablement import enabled_overrides
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.monitor.feed import MonitorFeed
    from lightfall.monitor.monitor_plugin import MonitorPlugin

MONITORS_KEY = "monitors.enabled"
FEEDS_KEY = "monitor_feeds.enabled"
FEED_INTERVALS_KEY = "monitor_feeds.interval"
FEED_SEVERITY_KEY = "monitor_feeds.severity"
MONITOR_KEYS: tuple[str, ...] = (MONITORS_KEY, FEEDS_KEY, FEED_INTERVALS_KEY, FEED_SEVERITY_KEY)

SEVERITIES: tuple[str, ...] = ("info", "warn", "critical")
DEFAULT_SEVERITY = "info"

# Targets (scope ids) whose copy of the old keys has already moved into the four maps.
# Local-only key (leading underscore): it lives in this console's YAML in every mode.
MIGRATED_PREF = "_monitors_enabled_migrated"
DISABLED_MONITORS_PREF = "disabled_monitor_plugins"
FORCED_ENABLED_MONITORS_PREF = "forced_enabled_monitor_plugins"
DISABLED_FEEDS_PREF = "disabled_monitor_feeds"
FEED_INTERVALS_PREF = "monitor_feed_intervals"
FEED_SEVERITY_PREF = "monitor_feed_advisor_severity"


class MonitorRegistry:
    _instance: MonitorRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._plugins: dict[str, MonitorPlugin] = {}
        self._feed_cache: dict[str, list[MonitorFeed]] = {}

    @classmethod
    def get_instance(cls) -> MonitorRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def register(self, plugin: MonitorPlugin) -> None:
        if plugin.name in self._plugins:
            logger.warning("monitor plugin '{}' already registered, replacing", plugin.name)
        self._plugins[plugin.name] = plugin
        self._feed_cache.pop(plugin.name, None)
        logger.debug("Registered monitor plugin: {} (priority={})", plugin.name, plugin.priority)

    def unregister(self, name: str) -> bool:
        self._feed_cache.pop(name, None)
        return self._plugins.pop(name, None) is not None

    def get_plugins(self) -> list[MonitorPlugin]:
        return list(self._plugins.values())

    def enabled_plugins(self) -> list[MonitorPlugin]:
        """Plugins the resolved ``monitors.enabled`` map leaves on, sorted by priority."""
        overrides = enabled_overrides(MONITORS_KEY)
        result = [p for p in self._plugins.values() if overrides.get(p.name, p.enabled_by_default)]
        result.sort(key=lambda p: p.priority)
        return result

    def enabled_feeds(self) -> list[MonitorFeed]:
        feeds: list[MonitorFeed] = []
        for plugin in self.enabled_plugins():
            cached = self._feed_cache.get(plugin.name)
            if cached is None:
                cached = plugin.create_feeds()
                self._feed_cache[plugin.name] = cached
            feeds.extend(cached)
        return feeds


# -- Per-feed settings ------------------------------------------------------


def _resolved(key: str) -> dict[str, Any]:
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        return PreferencesManager.get_instance().resolved_items(key)
    except Exception:  # noqa: BLE001 - early startup, tests: no preferences, no overrides
        return {}


def valid_interval(value: Any) -> bool:
    """A feed interval as stored: a whole number of seconds above zero."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def disabled_feed_names() -> set[str]:
    """Feeds the resolved ``monitor_feeds.enabled`` map switches off."""
    return {name for name, on in enabled_overrides(FEEDS_KEY).items() if on is False}


def feed_interval_overrides() -> dict[str, float]:
    """Feed name -> interval in seconds, from ``monitor_feeds.interval`` (invalid entries ignored)."""
    return {str(k): float(v) for k, v in _resolved(FEED_INTERVALS_KEY).items() if valid_interval(v)}


def advisor_floor(feed_name: str) -> str:
    """The lowest severity of ``feed_name`` the advisor is told about (default "info")."""
    value = _resolved(FEED_SEVERITY_KEY).get(feed_name)
    return value if value in SEVERITIES else DEFAULT_SEVERITY


# -- One-time migration from the old keys ------------------------------------


def _names(value: Any) -> list[str]:
    return sorted(str(x) for x in value) if isinstance(value, list) else []


def _legacy_maps(prefs: Any) -> dict[str, dict[str, Any]]:
    """What the five old keys meant, as the four new maps (invalid entries dropped)."""
    disabled = _names(prefs.get(DISABLED_MONITORS_PREF, None))
    forced = _names(prefs.get(FORCED_ENABLED_MONITORS_PREF, None))
    # A name in both lists was off (the old registry checked disabled first).
    monitors = {**dict.fromkeys(forced, True), **dict.fromkeys(disabled, False)}
    feeds = dict.fromkeys(_names(prefs.get(DISABLED_FEEDS_PREF, None)), False)
    intervals: dict[str, Any] = {}
    raw = prefs.get(FEED_INTERVALS_PREF, None)
    for name, value in (raw.items() if isinstance(raw, dict) else ()):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if valid_interval(value):
            intervals[str(name)] = value
        else:
            logger.info("Not migrating monitor feed interval {}={!r}: not whole seconds above 0", name, value)
    raw = prefs.get(FEED_SEVERITY_PREF, None)
    severities = {str(k): v for k, v in (raw.items() if isinstance(raw, dict) else ()) if v in SEVERITIES}
    return {MONITORS_KEY: monitors, FEEDS_KEY: feeds, FEED_INTERVALS_KEY: intervals,
            FEED_SEVERITY_KEY: severities}


def _migrated_targets(prefs: Any) -> set[str]:
    done = prefs.get(MIGRATED_PREF, None)
    return {str(x) for x in done} if isinstance(done, list) else set()


def migrate_monitor_prefs(prefs: Any) -> bool:
    """Carry the five old keys into the four maps at ``prefs.default_scope()``.

    Once per target (marker ``_monitors_enabled_migrated``). Same gating as plugins
    (``_may_migrate_to_server``): with server scopes only online, on an unbound console
    whose YAML is its user's; a bound console's values stay in its own console layer
    (the four keys are LOCAL_LAYER_KEYS), migrated to "local" at startup. Values already
    set at the target win, item by item. Returns True if this run migrated.
    """
    from lightfall.plugins.enablement import _may_migrate_to_server

    target = prefs.default_scope()
    done = _migrated_targets(prefs)
    if (target in done or not _may_migrate_to_server(prefs)
            or not all(prefs.writable_at(key, target) for key in MONITOR_KEYS)):
        return False
    for key, legacy in _legacy_maps(prefs).items():
        existing = prefs.layer_value(key, target)
        existing = dict(existing) if isinstance(existing, dict) else {}
        merged = {**legacy, **existing}
        if merged != existing:
            prefs.set(key, merged, scope=target)
            logger.info("Migrated monitor settings to {} at {}: {}", key, target,
                        {k: v for k, v in merged.items() if k not in existing})
    prefs.set(MIGRATED_PREF, sorted(done | {target}))
    return True


def _migrate_quietly(prefs: Any) -> None:
    """Migrate; never raise (a failure leaves the old keys unread until the next try)."""
    try:
        migrate_monitor_prefs(prefs)
    except Exception:  # noqa: BLE001 - must not break startup or the scope signal
        logger.exception("Migrating monitor settings failed")


_HOOKED: weakref.WeakSet[Any] = weakref.WeakSet()


def start_monitor_migration(prefs: Any = None) -> None:
    """Migrate now (this console's YAML before login) and on each online scope fetch."""
    try:
        if prefs is None:
            from lightfall.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
    except Exception as e:  # noqa: BLE001 - early startup, tests
        logger.debug("Cannot migrate monitor settings: {}", e)
        return
    _migrate_quietly(prefs)
    if prefs not in _HOOKED:
        _HOOKED.add(prefs)
        prefs.scopes_online.connect(lambda _info=None: _migrate_quietly(prefs))
