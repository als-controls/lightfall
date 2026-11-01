"""Monitor settings page: which monitors and feeds run, per scope, and how.

Two catalogs and two mappings over the monitor plugins in MonitorRegistry and their
feeds, each writing one scoped, item-merged setting (see ``lightfall.monitor.registry``):
``monitors.enabled``, ``monitor_feeds.enabled``, ``monitor_feeds.interval`` (whole
seconds above zero) and ``monitor_feeds.severity`` (the advisor's floor per feed). The
Inherited column shows what broader scopes or the code defaults give each item. Feeds
of a monitor plugin that is off for this user are drawn grey (still editable).

The advisor switch and the tick interval stay user settings. The scheduler and the
service read everything as it is needed, so no change asks for a restart.

The ``R1`` check: SettingSpec(policy="union", type=int / str) validates each item's
type, not the range or the choice set; the page's editors enforce those (an Integer
editor with minimum 1, a choice of the three severities), and the readers ignore any
stored value outside them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lightfall_settings import SettingSpec

from lightfall.monitor.registry import (
    DEFAULT_SEVERITY,
    FEED_INTERVALS_KEY,
    FEED_SEVERITY_KEY,
    FEEDS_KEY,
    MONITORS_KEY,
    MonitorRegistry,
)
from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.form.schema import (
    Catalog,
    CatalogItem,
    Choice,
    Integer,
    Mapping,
    Section,
    SettingField,
    Toggle,
)
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lightfall.monitor.monitor_plugin import MonitorPlugin

ADVISOR_ENABLED_PREF = "monitor_advisor_enabled"   # bool, default False
TICK_INTERVAL_PREF = "monitor_tick_interval"        # int seconds, default 60
DEFAULT_TICK_INTERVAL = 60
MAX_TICK_INTERVAL = 86400

SEVERITY_OPTIONS: tuple[tuple[str, str], ...] = (("info", "Info"), ("warn", "Warn"), ("critical", "Critical"))
MONITOR_OFF = "Not running: its monitor plugin is off"

_SCOPES = frozenset({"user", "branch", "beamline", "facility"})
_USER = frozenset({"user"})


def _plugins() -> tuple[list[MonitorPlugin], set[str]]:
    """Registered monitor plugins and the names of those enabled for this user."""
    try:
        registry = MonitorRegistry.get_instance()
        plugins = registry.get_plugins()
        enabled = {p.name for p in registry.enabled_plugins()}
    except Exception as e:  # noqa: BLE001 - an unavailable registry shows an empty list
        logger.warning("Failed to get MonitorRegistry: {}", e)
        return [], set()
    return plugins, enabled


def _monitor_items() -> list[CatalogItem]:
    """One catalog item per monitor plugin, sorted by category then display name."""
    plugins, _enabled = _plugins()
    return [
        CatalogItem(
            id=p.name,
            label=p.display_name,
            default_enabled=p.enabled_by_default,
            columns=(p.category.title(), p.description),
        )
        for p in sorted(plugins, key=lambda p: (p.category, p.display_name))
    ]


def _feeds() -> list[tuple[Any, MonitorPlugin, bool]]:
    """(feed, its plugin, plugin enabled) for every feed of every plugin, first name wins."""
    plugins, enabled = _plugins()
    out: list[tuple[Any, MonitorPlugin, bool]] = []
    seen: set[str] = set()
    for plugin in plugins:
        try:
            feeds = plugin.create_feeds()
        except Exception as e:  # noqa: BLE001 - one broken plugin must not empty the page
            logger.warning("Failed to create feeds for plugin '{}': {}", plugin.name, e)
            continue
        for feed in feeds:
            if feed.name not in seen:
                seen.add(feed.name)
                out.append((feed, plugin, plugin.name in enabled))
    return out


def _seconds(value: Any) -> Any:
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _feed_items(default: Any = None) -> list[CatalogItem]:
    return [
        CatalogItem(
            id=feed.name,
            label=feed.name,
            columns=(plugin.display_name,),
            default=default(feed) if callable(default) else default,
            inactive_reason="" if on else MONITOR_OFF,
        )
        for feed, plugin, on in _feeds()
    ]


def _interval_items() -> list[CatalogItem]:
    return _feed_items(lambda feed: _seconds(getattr(feed, "default_interval_s", None)))


def _severity_items() -> list[CatalogItem]:
    return _feed_items(DEFAULT_SEVERITY)


class MonitorSettingsPlugin(SettingsPlugin):
    """Monitor settings: monitor plugins, feeds, feed intervals and advisor severities per
    scope, plus the advisor switch and tick interval."""

    @property
    def name(self) -> str:
        return "monitor"

    @property
    def display_name(self) -> str:
        return "Monitor"

    @property
    def category(self) -> str:
        return "advanced"

    @property
    def priority(self) -> int:
        return 95

    def specs(self) -> Sequence[SettingSpec]:
        return [
            SettingSpec(key=ADVISOR_ENABLED_PREF, scopes=_USER, label="Monitor advisor"),
            SettingSpec(key=TICK_INTERVAL_PREF, scopes=_USER, label="Tick interval"),
            SettingSpec(key=MONITORS_KEY, type=bool, scopes=_SCOPES, policy="union",
                        label="Enabled monitor plugins"),
            SettingSpec(key=FEEDS_KEY, type=bool, scopes=_SCOPES, policy="union",
                        label="Enabled monitor feeds"),
            SettingSpec(key=FEED_INTERVALS_KEY, type=int, scopes=_SCOPES, policy="union",
                        label="Monitor feed intervals"),
            SettingSpec(key=FEED_SEVERITY_KEY, type=str, scopes=_SCOPES, policy="union",
                        label="Monitor feed advisor severity"),
        ]

    def sections(self) -> Sequence[Section]:
        feed_columns = ("Monitor",)
        return [
            Section(
                "Monitor",
                [
                    SettingField(ADVISOR_ENABLED_PREF, Toggle(), label="Enable monitor advisor (LLM)"),
                    SettingField(TICK_INTERVAL_PREF, Integer(1, MAX_TICK_INTERVAL), label="Tick interval",
                                 unit="s", placeholder=str(DEFAULT_TICK_INTERVAL)),
                ],
                description="The advisor summarizes observations with an LLM; the tick is how often "
                            "feeds are checked during a run.",
            ),
            Section(
                "Monitor plugins",
                [SettingField(MONITORS_KEY, Catalog(_monitor_items, columns=("Category", "Description")),
                              label="Monitor plugins")],
                description="Monitor plugins contribute the feeds below.",
            ),
            Section(
                "Feeds",
                [SettingField(FEEDS_KEY, Catalog(_feed_items, columns=feed_columns), label="Feeds")],
                description="Feeds of a monitor plugin that is off are shown grey.",
            ),
            Section(
                "Feed intervals",
                [SettingField(FEED_INTERVALS_KEY, Mapping(_interval_items, Integer(1, None), columns=feed_columns),
                              label="Feed intervals", unit="s")],
                description="How often each feed is evaluated, in whole seconds.",
            ),
            Section(
                "Advisor severity",
                [SettingField(FEED_SEVERITY_KEY, Mapping(_severity_items, Choice(SEVERITY_OPTIONS),
                                                         columns=feed_columns),
                              label="Advisor severity")],
                description="The lowest severity of each feed's observations that is passed to the advisor.",
            ),
        ]
