"""Copy to mine: make a shared (or built-in) item an editable local one in ~/lightfall.

Targets: ``plans/<name>.py``; ``plugins/<name>.py`` or ``plugins/<name>/``;
``agents/<name>.md``; ``skills/<name>/``. An existing local item of the same name is never
replaced unless the caller passes ``overwrite=True`` (after asking the user). Plans and
plugins are loaded through their running services, which commit the new file and touch
Qt objects, so call this on the Qt thread.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from lightfall.content.actions import ContentRow
from lightfall.content.publish import default_root
from lightfall.content.store import valid_item
from lightfall.utils.logging import logger

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".DS_Store", "Thumbs.db")


def _targets(row: ContentRow, root: Path) -> tuple[Path, list[Path]]:
    """The copy's path, and every local path that is the same item (all must be free)."""
    assert row.path is not None
    if row.type == "plan":
        dest = root / "plans" / f"{row.name}.py"
        return dest, [dest]
    if row.type == "plugin":
        file, folder = root / "plugins" / f"{row.name}.py", root / "plugins" / row.name
        return (folder if row.path.is_dir() else file), [file, folder]
    if row.type == "agent":
        dest = root / "agents" / f"{row.name}.md"
        return dest, [dest]
    if row.type == "skill":
        dest = root / "skills" / row.name
        return dest, [dest]
    raise ValueError(f"cannot copy a {row.type}")


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _place(src: Path, dest: Path) -> None:
    """Copy ``src`` beside ``dest`` first, then swap it in, so a failed copy changes nothing.

    An existing ``dest`` is moved aside (not deleted) before the swap and moved back if
    the swap fails (a sharing violation, an antivirus lock); it is deleted only after.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(dir=dest.parent, prefix=f".copy-{dest.name}-"))
    try:
        new = staged / dest.name
        if src.is_dir():
            shutil.copytree(src, new, ignore=_IGNORE)
        else:
            shutil.copy2(src, new)
        aside = staged / "previous"
        had_old = dest.exists() or dest.is_symlink()
        if had_old:
            os.rename(dest, aside)
        try:
            os.replace(new, dest)
        except BaseException:
            if had_old:
                os.rename(aside, dest)
            raise
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def _load(row: ContentRow, dest: Path, replaced: list[Path]) -> None:
    """Load a copied plan or plugin through its service, if that service is running."""
    msg = f"copied from {row.source}"
    if row.type == "plan":
        from lightfall.acquire.plans.user_plans import UserPlanService

        if UserPlanService._instance is not None:
            UserPlanService.get_instance().load_plan_from_file(dest, commit_msg=msg)
    elif row.type == "plugin":
        from lightfall.plugins.user_plugins import UserPluginService

        if UserPluginService._instance is None:
            return
        service = UserPluginService.get_instance()
        for old in replaced:  # a removed local plugin of the other form (file vs folder)
            if service.get_plugin_info(old) is not None:
                service.unload_plugin(old)
            service._commit_removal(old, msg)
        service._displace_synced(row.name)  # the local copy wins over the synced one
        service.load_plugin_from_file(dest, commit_msg=msg)
    elif row.type == "agent":
        from lightfall.agents.registry import AgentSpecRegistry

        if AgentSpecRegistry._instance is not None:
            AgentSpecRegistry.get_instance().reload()


def copy_to_mine(row: ContentRow, *, root: Path | None = None, overwrite: bool = False) -> Path:
    """Copy ``row``'s entry point into ``root`` (default ~/lightfall); returns the new path.

    Raises ValueError for a row with no file (core/package plans) or one that is already
    local, FileExistsError when a local item of that name exists and not ``overwrite``.
    """
    if row.path is None or not row.path.exists():
        raise ValueError(f"{row.type} {row.name!r} has no file to copy")
    if row.source == "local":
        raise ValueError(f"{row.type} {row.name!r} is already yours")
    if not valid_item(row.type, row.name):
        raise ValueError(f"cannot copy {row.type} {row.name!r}: not a valid name")
    root = root if root is not None else default_root()
    dest, same = _targets(row, root)
    taken = [p for p in same if p.exists() or p.is_symlink()]
    if taken and not overwrite:
        raise FileExistsError(f"You already have a {row.type} called {row.name!r} ({taken[0]})")
    replaced = [p for p in taken if p != dest]
    _place(row.path, dest)
    for other in replaced:
        _remove(other)
    logger.info("Copied {} {!r} from {} to {}", row.type, row.name, row.source, dest)
    _load(row, dest, replaced)
    return dest
