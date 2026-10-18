"""Facility bootstrap: fetch ``/.well-known/lightfall`` and layer it under the user's config.

``probe_health`` and ``fetch_bootstrap`` block (network, 10 s timeout): call them off the
GUI thread. ``apply_bootstrap`` writes ``bootstrap.yaml`` next to the user config and
(re)adds the non-mutable "bootstrap" layer at priority 15: above defaults (0) and
global/PROGRAMDATA (10), below beamline (20), user (30) and session (40). After login
the scoped settings still win.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from lightfall.setup.facilities import as_domain_list
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.config.manager import ConfigManager

SCHEMA = 1
TIMEOUT_S = 10.0
BOOTSTRAP_FILENAME = "bootstrap.yaml"
BOOTSTRAP_LAYER = "bootstrap"
BOOTSTRAP_PRIORITY = 15
WELL_KNOWN_PATH = "/.well-known/lightfall"
AUTH_PROVIDER_TYPES = frozenset({"local", "keycloak", "pam"})
AGENT_BACKENDS = frozenset({"claude-code", "agy"})


class NetworkError(Exception):
    """The server could not be reached or did not answer /health."""


class BootstrapError(Exception):
    """The bootstrap document could not be fetched or is not usable."""


class _Proxy(Enum):
    FROM_SETTINGS = "from-settings"


FROM_SETTINGS = _Proxy.FROM_SETTINGS


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class Bootstrap:
    server_url: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def facility_id(self) -> str:
        return str(_section(self.data, "facility").get("id", ""))

    @property
    def facility_name(self) -> str:
        return str(_section(self.data, "facility").get("name", ""))

    @property
    def proxy_domains(self) -> list[str]:
        return as_domain_list(_section(self.data, "network").get("proxy_domains"))

    def with_proxy_domains(self, domains: list[str] | tuple[str, ...]) -> Bootstrap:
        data = dict(self.data)
        data["network"] = {**_section(self.data, "network"), "proxy_domains": list(domains)}
        return Bootstrap(self.server_url, data)

    def summary(self) -> list[str]:
        """Plain words for what the bootstrap configures, for the wizard's ok note."""
        words: list[str] = []
        if _section(self.data, "auth"):
            words.append("sign-on")
        services = _section(self.data, "services")
        for key, word in (("tiled_url", "Tiled"), ("logbook_url", "logbook"),
                          ("ipc_nats_url", "NATS")):
            if _text(services.get(key)):
                words.append(word)
        backend = _section(self.data, "agent").get("backend")
        if backend == "agy":
            words.append("assistant (Gemini)")
        elif backend == "claude-code":
            words.append("assistant (Claude)")
        return words


def resolve_proxy(url: str, proxy: str | None | _Proxy = FROM_SETTINGS) -> str | None:
    """The proxy for ``url``: an explicit value, or Settings → Network Proxy."""
    if proxy is not FROM_SETTINGS:
        return proxy  # type: ignore[return-value]
    try:
        from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider

        return ProxySettingsProvider.should_use_proxy_for_url(url)
    except Exception as exc:  # noqa: BLE001 - proxy lookup is best-effort
        logger.debug("Proxy lookup failed for {}: {}", url, exc)
        return None


def _plain(exc: Exception, timeout: float) -> str:
    import httpx

    if isinstance(exc, httpx.TimeoutException):
        return f"no answer within {timeout:g} s"
    if isinstance(exc, httpx.ProxyError):
        return "the proxy refused the connection"
    if isinstance(exc, httpx.ConnectError):
        return "could not connect"
    return str(exc) or type(exc).__name__


def _get(url: str, *, proxy: str | None, timeout: float) -> Any:
    import httpx

    kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": True}
    if proxy:
        kwargs["proxy"] = proxy
    with httpx.Client(**kwargs) as client:
        return client.get(url, headers={"Accept": "application/json"})


def probe_health(server_url: str, *, proxy: str | None | _Proxy = FROM_SETTINGS,
                 timeout: float = TIMEOUT_S) -> None:
    """Raise ``NetworkError`` unless ``<server>/health`` answers 200. Blocking."""
    base = server_url.rstrip("/")
    try:
        resp = _get(base + "/health", proxy=resolve_proxy(base, proxy), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a plain message
        raise NetworkError(_plain(exc, timeout)) from exc
    if resp.status_code != 200:
        raise NetworkError(f"{base}/health answered HTTP {resp.status_code}")


def fetch_bootstrap(server_url: str, *, proxy: str | None | _Proxy = FROM_SETTINGS,
                    timeout: float = TIMEOUT_S) -> Bootstrap:
    """GET ``<server>/.well-known/lightfall``; raise ``BootstrapError`` if unusable. Blocking."""
    base = server_url.rstrip("/")
    try:
        resp = _get(base + WELL_KNOWN_PATH, proxy=resolve_proxy(base, proxy), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a plain message
        raise BootstrapError(_plain(exc, timeout)) from exc
    if resp.status_code != 200:
        raise BootstrapError(f"the server answered HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise BootstrapError("the server's answer is not valid JSON") from None
    if not isinstance(data, dict):
        raise BootstrapError("the server's answer is not a JSON object")
    schema = data.get("schema")
    if isinstance(schema, bool) or schema != SCHEMA:
        raise BootstrapError(f"unsupported bootstrap schema {schema!r} (expected {SCHEMA})")
    return Bootstrap(server_url=base, data=data)


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _backend(value: object) -> str | None:
    return value if isinstance(value, str) and value in AGENT_BACKENDS else None


def _true(value: object) -> bool | None:
    return True if value is True else None


def _domains(value: object) -> list[str] | None:
    return as_domain_list(value) or None


@dataclass(frozen=True)
class _Pref:
    """One allow-listed bootstrap preference: where it comes from and how it is checked."""

    target: str  # the key under ``preferences``
    source: tuple[str, str] | None  # (section, name) in the bootstrap document
    clean: Callable[[object], object | None]  # the value to keep, or None to drop it


# The only preferences a bootstrap may set. Mirrors the server's SECTIONS in
# lightfall-logbook well_known.py; tiled_enabled is derived from tiled_url.
BOOTSTRAP_PREFS: tuple[_Pref, ...] = (
    _Pref("tiled_url", ("services", "tiled_url"), _str_or_none),
    _Pref("logbook_url", ("services", "logbook_url"), _str_or_none),
    _Pref("ipc_nats_url", ("services", "ipc_nats_url"), _str_or_none),
    _Pref("tiled_enabled", None, _true),
    _Pref("agent_backend", ("agent", "backend"), _backend),
    _Pref("agy_gcp_project", ("agent", "agy_gcp_project"), _str_or_none),
    _Pref("agy_gcp_location", ("agent", "agy_gcp_location"), _str_or_none),
    _Pref("proxy_auto_detect_domains", ("network", "proxy_domains"), _domains),
)
_PREF_RULES = {p.target: p for p in BOOTSTRAP_PREFS}
_PREF_SOURCES = frozenset(p.source for p in BOOTSTRAP_PREFS if p.source is not None)
_PROVIDER_FIELDS = ("server_url", "realm", "client_id")
_FACILITY_FIELDS = ("id", "name", "server_url")


def _provider_value(name: str, value: object) -> object | None:
    """The auth.provider value to keep, or None to drop it."""
    if name == "type":
        return value if isinstance(value, str) and value in AUTH_PROVIDER_TYPES else None
    if name in _PROVIDER_FIELDS:
        return _str_or_none(value)
    return None


def _validated_layer(layer: dict[str, Any]) -> dict[str, Any]:
    """Check the layer on its own against LFConfig; drop what would invalidate it.

    A failing top-level section is dropped with a warning; if what is left still fails,
    the whole layer is dropped. A bad bootstrap must never invalidate LFConfig.
    """
    from lightfall.config import schema

    def failures(candidate: dict[str, Any]) -> set[str] | None:
        try:
            schema.LFConfig.model_validate(candidate)
        except Exception as exc:  # noqa: BLE001 - any failure means "not usable"
            try:
                return {str(e["loc"][0]) for e in exc.errors() if e.get("loc")}  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - not a ValidationError: blame nothing
                return set()
        return None

    bad = failures(layer)
    if bad is None:
        return layer
    for section in sorted(bad & set(layer)):
        logger.warning("Ignoring bootstrap section {!r}: it does not fit the config schema",
                       section)
    layer = {k: v for k, v in layer.items() if k not in bad}
    if failures(layer) is not None:
        logger.warning("Ignoring the bootstrap layer: it does not fit the config schema")
        return {}
    return layer


def bootstrap_to_layer(bootstrap: Bootstrap) -> dict[str, Any]:
    """Map the bootstrap document onto config keys, keeping only the allow-list."""
    data = bootstrap.data
    layer: dict[str, Any] = {"setup": {"facility": {
        "id": bootstrap.facility_id,
        "name": bootstrap.facility_name,
        "server_url": bootstrap.server_url,
    }}}

    provider: dict[str, Any] = {}
    auth = _section(data, "auth")
    for name, key in (("type", "provider"), *((n, n) for n in _PROVIDER_FIELDS)):
        if key not in auth:
            continue
        value = _provider_value(name, auth[key])
        if value is None:
            logger.warning("Ignoring bootstrap auth.{} {!r}", key, auth[key])
        else:
            provider[name] = value
    if provider:
        layer["auth"] = {"provider": provider}

    for section in sorted({s for s, _ in _PREF_SOURCES}):
        for key in _section(data, section):
            if (section, key) not in _PREF_SOURCES:
                logger.warning("Ignoring bootstrap {}.{}: not a setting a bootstrap may set",
                               section, key)
    prefs: dict[str, Any] = {}
    for pref in BOOTSTRAP_PREFS:
        if pref.source is None:
            continue
        section, name = pref.source
        raw = _section(data, section).get(name)
        value = pref.clean(raw)
        if value is not None:
            prefs[pref.target] = value
        elif raw is not None:
            logger.warning("Ignoring bootstrap {}.{} {!r}", section, name, raw)
    if "tiled_url" in prefs:
        prefs["tiled_enabled"] = True
    if prefs:
        layer["preferences"] = prefs
    return _validated_layer(layer)


def _sanitize_layer(data: dict[str, Any]) -> dict[str, Any]:
    """Apply the bootstrap allow-list to a bootstrap.yaml read from disk."""
    out: dict[str, Any] = {}
    for key in data:
        if key not in ("setup", "auth", "preferences"):
            logger.warning("Ignoring bootstrap.yaml section {!r}", key)

    setup = data.get("setup")
    if isinstance(setup, dict):
        for key in setup:
            if key != "facility":
                logger.warning("Ignoring bootstrap.yaml setup.{}", key)
        facility = setup.get("facility")
        if isinstance(facility, dict):
            kept = {}
            for key, value in facility.items():
                if key in _FACILITY_FIELDS and isinstance(value, str):
                    kept[key] = value
                else:
                    logger.warning("Ignoring bootstrap.yaml setup.facility.{} {!r}", key, value)
            if kept:
                out["setup"] = {"facility": kept}
        elif "facility" in setup:
            logger.warning("Ignoring bootstrap.yaml setup.facility {!r}", facility)
    elif "setup" in data:
        logger.warning("Ignoring bootstrap.yaml setup {!r}", setup)

    auth = data.get("auth")
    if isinstance(auth, dict):
        for key in auth:
            if key != "provider":
                logger.warning("Ignoring bootstrap.yaml auth.{}", key)
        provider = auth.get("provider")
        if isinstance(provider, dict):
            kept = {}
            for name, value in provider.items():
                clean = _provider_value(name, value) if isinstance(name, str) else None
                if clean is None:
                    logger.warning("Ignoring bootstrap.yaml auth.provider.{} {!r}", name, value)
                else:
                    kept[name] = clean
            if kept:
                out["auth"] = {"provider": kept}
        elif "provider" in auth:
            logger.warning("Ignoring bootstrap.yaml auth.provider {!r}", provider)
    elif "auth" in data:
        logger.warning("Ignoring bootstrap.yaml auth {!r}", auth)

    prefs = data.get("preferences")
    if isinstance(prefs, dict):
        kept = {}
        for key, value in prefs.items():
            rule = _PREF_RULES.get(key) if isinstance(key, str) else None
            clean = rule.clean(value) if rule is not None else None
            if clean is None:
                logger.warning("Ignoring bootstrap.yaml preferences.{} {!r}", key, value)
            else:
                kept[key] = clean
        if kept:
            out["preferences"] = kept
    elif "preferences" in data:
        logger.warning("Ignoring bootstrap.yaml preferences {!r}: not a mapping", prefs)
    return _validated_layer(out)


def bootstrap_path(config: ConfigManager) -> Path:
    return config.get_user_config_path().parent / BOOTSTRAP_FILENAME


def has_bootstrap(config: ConfigManager) -> bool:
    return bootstrap_path(config).is_file()


def _install_layer(config: ConfigManager, data: dict[str, Any], path: Path) -> None:
    from lightfall.config.layers import ConfigLayer

    config.add_layer(ConfigLayer(name=BOOTSTRAP_LAYER, priority=BOOTSTRAP_PRIORITY, data=data,
                                 source=path, mutable=False))


def apply_bootstrap(bootstrap: Bootstrap, config: ConfigManager) -> Path:
    """Write bootstrap.yaml (atomically) and (re)add the bootstrap layer. Returns the path."""
    layer = bootstrap_to_layer(bootstrap)
    path = bootstrap_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".bootstrap-", suffix=".yaml", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(layer, fh, default_flow_style=False, sort_keys=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _install_layer(config, layer, path)
    logger.info("Applied facility bootstrap from {} ({})", bootstrap.server_url, path)
    return path


def load_bootstrap_layer(config: ConfigManager) -> bool:
    """At startup: add the bootstrap layer when bootstrap.yaml exists. Never raises."""
    path = bootstrap_path(config)
    if not path.is_file():
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a broken file must not stop startup
        logger.warning("Ignoring unreadable {}: {}", path, exc)
        return False
    if not isinstance(data, dict):
        logger.warning("Ignoring {}: not a mapping", path)
        return False
    try:
        _install_layer(config, _sanitize_layer(data), path)
    except Exception as exc:  # noqa: BLE001 - a broken file must not stop startup
        logger.warning("Ignoring unusable {}: {}", path, exc)
        return False
    return True
