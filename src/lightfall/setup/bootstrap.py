"""Facility bootstrap: fetch ``/.well-known/lightfall`` and layer it under the user's config.

``probe_health`` and ``fetch_bootstrap`` block (network, 10 s timeout): call them off the
GUI thread. ``apply_bootstrap`` writes ``bootstrap.yaml`` next to the user config and
(re)adds the non-mutable "bootstrap" layer at priority 15: above defaults (0) and
global/PROGRAMDATA (10), below beamline (20), user (30) and session (40). After login
the scoped settings still win.
"""
from __future__ import annotations

import os
import re
import tempfile
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
_PREF_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_SECRET_MARKERS = ("api_key", "token", "secret", "password", "dsn")


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


def _pref_key(key: object) -> bool:
    return (isinstance(key, str) and bool(_PREF_NAME.match(key))
            and not any(m in key for m in _SECRET_MARKERS))


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


def bootstrap_to_layer(bootstrap: Bootstrap) -> dict[str, Any]:
    """Map the bootstrap document onto config keys, dropping anything invalid."""
    data = bootstrap.data
    layer: dict[str, Any] = {"setup": {"facility": {
        "id": bootstrap.facility_id,
        "name": bootstrap.facility_name,
        "server_url": bootstrap.server_url,
    }}}

    provider: dict[str, Any] = {}
    auth = _section(data, "auth")
    ptype = auth.get("provider")
    if ptype in AUTH_PROVIDER_TYPES:
        provider["type"] = ptype
    elif ptype is not None:
        logger.warning("Ignoring bootstrap auth.provider {!r}", ptype)
    for name in ("server_url", "realm", "client_id"):
        if _text(auth.get(name)):
            provider[name] = auth[name]
    if provider:
        layer["auth"] = {"provider": provider}

    prefs: dict[str, Any] = {}
    for key, value in _section(data, "services").items():
        if _pref_key(key) and _text(value):
            prefs[key] = value
    if "tiled_url" in prefs:
        prefs["tiled_enabled"] = True
    agent = _section(data, "agent")
    if agent.get("backend") in AGENT_BACKENDS:
        prefs["agent_backend"] = agent["backend"]
    for key, value in agent.items():
        if isinstance(key, str) and key.startswith("agy_") and _pref_key(key) and _text(value):
            prefs[key] = value
    domains = bootstrap.proxy_domains
    if domains:
        prefs["proxy_auto_detect_domains"] = domains
    if prefs:
        layer["preferences"] = prefs
    return layer


def _sanitize_layer(data: dict[str, Any]) -> dict[str, Any]:
    """Drop a hand-edited auth.provider.type the config schema would reject."""
    provider = _section(_section(data, "auth"), "provider")
    if "type" in provider and provider["type"] not in AUTH_PROVIDER_TYPES:
        logger.warning("Ignoring bootstrap auth.provider.type {!r}", provider["type"])
        del provider["type"]
    return data


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
    _install_layer(config, _sanitize_layer(data), path)
    return True
