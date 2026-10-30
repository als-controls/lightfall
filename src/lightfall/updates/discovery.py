"""Find installed Lightfall distributions and how each one was installed."""

from __future__ import annotations

import importlib.metadata
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from urllib.request import url2pathname

from packaging.utils import canonicalize_name

from lightfall.updates.git import GitError, run_git

InstallKind = Literal["source", "pypi"]
PLUGIN_GROUP = "lightfall.plugins"


@dataclass(frozen=True)
class InstalledDist:
    """One installed distribution: an editable git checkout or an index install."""

    name: str
    version: str
    kind: InstallKind
    repo_root: Path | None


def git_toplevel(path: Path) -> Path | None:
    """The git work tree containing ``path``, or None if it is not in one."""
    if not path.is_dir():
        return None
    try:
        return Path(run_git(path, "rev-parse", "--show-toplevel"))
    except GitError:
        return None


def _file_url_path(url: str) -> Path | None:
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    return Path(url2pathname(parsed.netloc + parsed.path if parsed.netloc else parsed.path))


def classify(
    dist: importlib.metadata.Distribution,
    toplevel: Callable[[Path], Path | None] = git_toplevel,
) -> InstalledDist | None:
    """Classify ``dist``; None for install kinds we don't check (VCS, archives, ...)."""
    name = canonicalize_name(dist.metadata["Name"])
    raw = dist.read_text("direct_url.json")
    if raw is None:
        return InstalledDist(name, dist.version, "pypi", None)
    try:
        info = json.loads(raw)
    except ValueError:
        return None
    if not info.get("dir_info", {}).get("editable"):
        return None
    path = _file_url_path(info.get("url", ""))
    root = toplevel(path) if path is not None else None
    if root is None:
        return None
    return InstalledDist(name, dist.version, "source", root)


def _entry_point_dist_names() -> list[str]:
    names = []
    for ep in importlib.metadata.entry_points(group=PLUGIN_GROUP):
        if ep.dist is not None:
            names.append(ep.dist.name)
    return names


def discover_installed(
    dists: Iterable[importlib.metadata.Distribution] | None = None,
    entry_point_dists: Iterable[str] | None = None,
) -> list[InstalledDist]:
    """Every installed Lightfall distribution we know how to check.

    A distribution counts if its name starts with ``lightfall`` or it provides a
    ``lightfall.plugins`` entry point.
    """
    if dists is None:
        dists = importlib.metadata.distributions()
    if entry_point_dists is None:
        entry_point_dists = _entry_point_dist_names()
    plugin_names = {canonicalize_name(n) for n in entry_point_dists}
    found: dict[str, InstalledDist] = {}
    for dist in dists:
        name = canonicalize_name(dist.metadata["Name"] or "")
        if not name or name in found:
            continue
        if not (name.startswith("lightfall") or name in plugin_names):
            continue
        item = classify(dist)
        if item is not None:
            found[name] = item
    return list(found.values())


def group_by_repo(items: list[InstalledDist]) -> dict[Path, tuple[str, ...]]:
    """Source installs keyed by repo root, each with the distributions it ships."""
    repos: dict[Path, list[str]] = {}
    for item in items:
        if item.kind == "source" and item.repo_root is not None:
            repos.setdefault(item.repo_root, []).append(item.name)
    return {root: tuple(names) for root, names in repos.items()}
