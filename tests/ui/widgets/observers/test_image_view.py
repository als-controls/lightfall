from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from lucid.ui.widgets.observers import CameraBase


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class FakeCamera(CameraBase):
    """CameraBase test double. Emits `n_frames` random frames from a background thread."""

    def __init__(self, shape: tuple[int, int] = (64, 96), n_frames: int = 3):
        self._shape = shape
        self._n_frames = n_frames
        self._on_frame = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._open_calls = 0
        self._close_calls = 0

    def open(self) -> None:
        self._open_calls += 1

    def close(self) -> None:
        self._close_calls += 1

    def start_stream(self, on_frame=None) -> None:
        self._on_frame = on_frame
        self._stop.clear()
        self._thread = threading.Thread(target=self._emit_loop, daemon=True)
        self._thread.start()

    def stop_stream(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def get_latest_frame(self):
        return None

    def _emit_loop(self) -> None:
        for i in range(self._n_frames):
            if self._stop.is_set():
                return
            img = (np.random.rand(*self._shape) * 255).astype(np.uint8)
            if self._on_frame is not None:
                self._on_frame(img)
            time.sleep(0.05)


def test_cameraimageview_requires_camera_to_start(qapp):
    from lucid.ui.widgets.observers import CameraImageView
    view = CameraImageView()
    with pytest.raises(RuntimeError, match="no camera"):
        view.start()


def test_cameraimageview_set_camera_late(qapp):
    from lucid.ui.widgets.observers import CameraImageView
    view = CameraImageView()
    fake = FakeCamera()
    view.set_camera(fake)
    assert "idle" in view._status.text()


def test_cameraimageview_receives_frames(qapp):
    """End-to-end: construct with FakeCamera, start, pump events, verify frames rendered."""
    from lucid.ui.widgets.observers import CameraImageView
    fake = FakeCamera(shape=(32, 48), n_frames=3)
    view = CameraImageView(camera=fake)

    view.start()
    deadline = time.time() + 3.0
    while view._frames_seen < 3 and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    view.stop()
    qapp.processEvents()

    assert view._frames_seen == 3, f"expected 3 frames, got {view._frames_seen}"
    assert fake._open_calls == 1
    assert fake._close_calls == 1


def test_cameraimageview_cannot_change_camera_while_streaming(qapp):
    from lucid.ui.widgets.observers import CameraImageView
    fake = FakeCamera(n_frames=10)
    view = CameraImageView(camera=fake)
    view.start()
    try:
        with pytest.raises(RuntimeError, match="stop the current stream"):
            view.set_camera(FakeCamera())
    finally:
        view.stop()
