"""The packaged facility list (config/facilities.yaml) and server-URL handling."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from urllib.parse import urlsplit, urlunsplit

import yaml

from lightfall.utils.logging import logger

_STRIP_SUFFIXES = ("/.well-known/lightfall", "/health")


@dataclass(frozen=True)
class Facility:
    id: str
    name: str
    server_url: str
    short_name: str = ""
    subtitle: str = ""
    proxy_domains: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.short_name or self.name


def as_domain_list(value: object) -> list[str]:
    """Proxy domain patterns from a bootstrap or preference value (str or list)."""
    items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [item.strip().lower() for item in items if isinstance(item, str) and item.strip()]


def normalize_server_url(text: str) -> str:
    """A facility server's base URL; raises ``ValueError`` with a message for the user."""
    url = text.strip()
    if not url:
        raise ValueError("Enter your facility's Lightfall server URL.")
    if any(ch.isspace() for ch in url):
        raise ValueError("The server URL can't contain spaces.")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("The server URL must start with http:// or https://.")
    if not parts.hostname:
        raise ValueError("The server URL needs a host name.")
    path = parts.path.rstrip("/")
    for suffix in _STRIP_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


def other_facility(server_url: str) -> Facility:
    url = normalize_server_url(server_url)
    return Facility(id="other", name=urlsplit(url).hostname or url, server_url=url)


def load_facilities(text: str | None = None) -> list[Facility]:
    if text is None:
        text = resources.files("lightfall.config").joinpath("facilities.yaml").read_text(
            encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.error("facilities.yaml is not valid YAML: {}", exc)
        return []
    items = data.get("facilities", []) if isinstance(data, dict) else []
    out: list[Facility] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            logger.warning("Skipping facility entry {!r}: not a mapping", item)
            continue
        try:
            reach = item.get("reach") if isinstance(item.get("reach"), dict) else {}
            out.append(Facility(
                id=str(item["id"]),
                name=str(item["name"]),
                server_url=normalize_server_url(str(item["server_url"])),
                short_name=str(item.get("short_name", "")),
                subtitle=str(item.get("subtitle", "")),
                proxy_domains=tuple(as_domain_list(reach.get("proxy_domains"))),
            ))
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping facility entry {!r}: {}", item, exc)
    return out
