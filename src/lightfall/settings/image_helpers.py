"""Pure helpers shared across UI code that displays lightfall-logbook image
artifacts (profile picture, fragment images, etc.).

Keep this module dependency-free of QWidget — it must be safe to import
from worker threads.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QImage

if TYPE_CHECKING:
    from lightfall.settings.user_settings_client import UserSettingsClient


def _fetch_qimage(client: "UserSettingsClient", image_id: str) -> QImage:
    """Download `image_id` via `client` and decode the bytes into a QImage.

    Designed to run on a worker thread; the returned QImage is
    thread-safe to pass back to the GUI thread (do the QPixmap
    conversion there).

    Returns a null QImage (image.isNull() == True) on any decode
    failure. HTTP failures propagate as UserSettingsError.
    """
    data, _content_type = client.download_image(image_id)
    image = QImage()
    image.loadFromData(data)
    return image
