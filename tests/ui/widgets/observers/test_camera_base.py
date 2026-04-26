from __future__ import annotations

import pytest

from lucid.ui.widgets.observers import CameraBase


def test_camerabase_is_abstract():
    """Can't instantiate CameraBase directly."""
    with pytest.raises(TypeError):
        CameraBase()  # type: ignore[abstract]


def test_camerabase_context_manager_shape():
    """Concrete __enter__ / __exit__ live on the base — subclasses inherit."""
    assert CameraBase.__enter__ is not None
    assert CameraBase.__exit__ is not None
