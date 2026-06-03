"""Tests for image helpers used by both the profile dialog and avatar widget."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtGui import QImage


def test_fetch_qimage_returns_qimage_for_png_bytes(qapp):
    """_fetch_qimage downloads via the client and decodes bytes to QImage."""
    from lightfall.settings.image_helpers import _fetch_qimage

    # Smallest valid PNG: 1x1 transparent.
    png = bytes.fromhex(
        "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000D49444154789C6360606060000000050001A5F645400000000049454E44AE426082"
    )
    client = MagicMock()
    client.download_image.return_value = (png, "image/png")

    image = _fetch_qimage(client, "img-abc")
    assert isinstance(image, QImage)
    assert not image.isNull()
    client.download_image.assert_called_once_with("img-abc")


def test_fetch_qimage_returns_null_image_on_garbage(qapp):
    from lightfall.settings.image_helpers import _fetch_qimage

    client = MagicMock()
    client.download_image.return_value = (b"not an image", "image/png")
    image = _fetch_qimage(client, "img-bad")
    assert isinstance(image, QImage)
    assert image.isNull()
