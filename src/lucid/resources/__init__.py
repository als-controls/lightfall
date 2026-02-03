"""LUCID application resources.

This package contains static assets like images and icons.
Use the helper functions to load resources in a platform-independent way.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from lucid.utils.logging import logger

# Cache for loaded pixmaps (path -> pixmap)
_pixmap_cache: dict[str, QPixmap] = {}


def get_resource_path(filename: str) -> Path:
    """Get the path to a resource file.

    Args:
        filename: Resource filename (e.g., 'images/logo.png').

    Returns:
        Path to the resource file.
    """
    return Path(__file__).parent / filename


def get_logo_pixmap(size: int | None = None) -> QPixmap:
    """Get the LUCID logo as a QPixmap.

    Args:
        size: Optional size to scale the logo to (maintains aspect ratio).
              If None, returns the original size.

    Returns:
        QPixmap of the logo.
    """
    logo_path = get_resource_path("images/logo.png")
    cache_key = f"logo:{size}"

    # Check cache first
    if cache_key in _pixmap_cache:
        return _pixmap_cache[cache_key]

    # Load the pixmap
    path_str = str(logo_path)
    logger.debug("Loading logo from: {}", path_str)

    if not logo_path.exists():
        logger.warning("Logo file not found: {}", path_str)
        return QPixmap()

    pixmap = QPixmap(path_str)

    if pixmap.isNull():
        logger.warning("Failed to load logo pixmap from: {}", path_str)
        return pixmap

    logger.debug("Loaded logo: {}x{}", pixmap.width(), pixmap.height())

    # Scale if requested
    if size is not None:
        pixmap = pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        logger.debug("Scaled logo to: {}x{}", pixmap.width(), pixmap.height())

    # Cache and return
    _pixmap_cache[cache_key] = pixmap
    return pixmap


__all__ = ["get_resource_path", "get_logo_pixmap"]
