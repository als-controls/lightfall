"""Look up the newest release of a package on the index (public PyPI for now)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from packaging.version import InvalidVersion, Version

from lightfall.utils.logging import logger

#: The one place the index lives; switch it here to move to a facility mirror.
DEFAULT_INDEX_URL = "https://pypi.org"


@dataclass(frozen=True)
class PypiState:
    """An index-installed distribution and the newest release found for it."""

    name: str
    installed: str
    latest: str | None

    @property
    def has_update(self) -> bool:
        if self.latest is None:
            return False
        try:
            return Version(self.latest) > Version(self.installed)
        except InvalidVersion:
            return False


def latest_version(
    name: str,
    installed: str,
    index_url: str = DEFAULT_INDEX_URL,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> str | None:
    """The newest non-yanked release of ``name``; None if the index can't say.

    Pre-releases are only considered when ``installed`` is itself a pre-release.
    """
    url = f"{index_url.rstrip('/')}/pypi/{name}/json"
    try:
        if client is None:
            with httpx.Client(timeout=timeout) as owned:
                response = owned.get(url)
        else:
            response = client.get(url, timeout=timeout)
        if response.status_code != 200:
            logger.debug("Update check: {} -> HTTP {}", url, response.status_code)
            return None
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.debug("Update check: {} failed: {}", url, e)
        return None
    try:
        allow_pre = Version(installed).is_prerelease
    except InvalidVersion:
        allow_pre = False
    best: Version | None = None
    for raw, files in (data.get("releases") or {}).items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if version.is_prerelease and not allow_pre:
            continue
        if files and all(f.get("yanked") for f in files):
            continue
        if best is None or version > best:
            best = version
    return str(best) if best is not None else None
