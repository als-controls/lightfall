"""One-time migration of the user data directory from the legacy Lightfall name."""
from __future__ import annotations

from pathlib import Path

from loguru import logger


def migrate_legacy_data_dir(home: Path | None = None) -> bool:
    """Move ``~/lucid`` to ``~/lightfall`` once, if only the legacy dir exists.

    Returns True if a migration was performed, False otherwise. No-ops (and
    leaves both in place) if the new directory already exists.
    """
    home = home or Path.home()
    legacy = home / "lucid"
    current = home / "lightfall"
    if current.exists() or not legacy.exists():
        return False
    legacy.rename(current)
    logger.info("Migrated legacy data directory {} -> {}", legacy, current)
    return True
