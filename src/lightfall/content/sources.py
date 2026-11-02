"""Synced content items as loaders see them: one ``SourceItem`` per installed item.

Items come from the store's installed index (``ContentStore.listing``) and the folder
``item_path`` names, never from listing folders. An item whose folder is missing or
whose files don't match the layout for its type is skipped with a warning.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lightfall.content.store import ContentStore, default_content_dir
from lightfall.scopes.site import get_site_bind
from lightfall.utils.logging import logger

# Among non-package sources the more specific wins; core and package rank 0.
SOURCE_RANK_SPECIFIC = {"facility": 1, "beamline": 2, "branch": 3, "local": 4}


@dataclass(frozen=True)
class SourceItem:
    type: str
    name: str
    source: str  # "facility", "beamline:7.0.1", "branch:7.0.1.1", "local", "package", "core"
    path: Path  # entry point: plan/plugin .py, plugin package folder, agent .md, skill folder
    revision: int | None = None


def specificity(source: str) -> int:
    if source in ("core", "package"):
        return 0
    return SOURCE_RANK_SPECIFIC[source.partition(":")[0]]


def _entry_point(type_: str, name: str, folder: Path) -> Path | None:
    """The item's entry point inside its folder, or None when the layout is wrong."""
    if type_ == "plan":
        entry = folder / f"{name}.py"
        return entry if entry.is_file() else None
    if type_ == "plugin":
        if (folder / "__init__.py").is_file():
            return folder
        entry = folder / f"{name}.py"
        return entry if entry.is_file() else None
    if type_ == "agent":
        mds = sorted(p for p in folder.glob("*.md") if p.is_file())
        return mds[0] if len(mds) == 1 else None
    if type_ == "skill":
        return folder if (folder / "SKILL.md").is_file() else None
    return None


def synced_items(store: ContentStore, bind: str, type_: str) -> list[SourceItem]:
    """Installed items of ``type_`` for ``bind``, facility first and branch last."""
    out: list[SourceItem] = []
    for scope, _ in store.sources(bind):
        listing = store.listing(scope)
        if listing is None:
            continue
        for item in listing.items:
            if item.type != type_:
                continue
            try:
                folder = store.item_path(scope, type_, item.name, item.revision)
            except ValueError as e:
                logger.warning("Skipping synced {} {!r} at {}: {}", type_, item.name, scope, e)
                continue
            if not folder.is_dir():
                logger.warning("Skipping synced {} {!r} at {}: folder {} is missing",
                               type_, item.name, scope, folder)
                continue
            entry = _entry_point(type_, item.name, folder)
            if entry is None:
                logger.warning("Skipping synced {} {!r} at {}: unexpected layout in {}",
                               type_, item.name, scope, folder)
                continue
            out.append(SourceItem(type_, item.name, scope, entry, item.revision))
    return out


def default_synced_items(type_: str) -> list[SourceItem]:
    """``synced_items`` for this machine's store and site bind; ``[]`` if anything fails."""
    try:
        return synced_items(ContentStore(default_content_dir()), get_site_bind(), type_)
    except Exception as e:
        logger.warning("Could not read synced {} items: {}", type_, e)
        return []
