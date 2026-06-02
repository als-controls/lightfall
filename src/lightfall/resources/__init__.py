"""LUCID application resources.

This package contains static assets like images and icons.
Use the helper functions to load resources in a platform-independent way.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap

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


# Cache for app icon
_app_icon: QIcon | None = None


def get_app_icon() -> QIcon:
    """Get the LUCID application icon.

    Uses .ico format on Windows for best taskbar compatibility,
    .png on other platforms.

    Returns:
        QIcon for use as window/taskbar icon.
    """
    global _app_icon

    if _app_icon is not None:
        return _app_icon

    # Use .ico on Windows for best taskbar compatibility
    if sys.platform == "win32":
        icon_path = get_resource_path("images/icon.ico")
    else:
        icon_path = get_resource_path("images/icon.png")

    path_str = str(icon_path)
    logger.debug("Loading app icon from: {}", path_str)

    if not icon_path.exists():
        logger.warning("App icon not found: {}", path_str)
        return QIcon()

    # Use addFile for explicit icon loading with all sizes
    _app_icon = QIcon()
    _app_icon.addFile(path_str)

    if _app_icon.isNull():
        logger.warning("Failed to load app icon from: {}", path_str)
    else:
        # Log available sizes for debugging
        sizes = _app_icon.availableSizes()
        logger.debug("Loaded app icon with {} sizes: {}", len(sizes), sizes)

    return _app_icon


__all__ = ["get_resource_path", "get_logo_pixmap", "get_app_icon"]
