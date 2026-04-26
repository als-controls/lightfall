"""pyqtgraph-based widget for live camera observation, generic over CameraBase."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lucid.ui.widgets.observers.camera import CameraBase


class CameraImageView(QWidget):
    """Minimal live-image widget over any CameraBase.

    Construct with a camera, or construct empty and call ``set_camera(cam)`` later.
    Click Start to open the camera and begin streaming. The receiver thread's frames
    are marshalled onto the GUI thread via a Qt Signal, so the pipeline is thread-safe.
    """

    frame_received = Signal(np.ndarray)

    def __init__(self, camera: CameraBase | None = None, parent=None):
        super().__init__(parent)
        self._camera: CameraBase | None = camera
        self._streaming = False
        self._frames_seen = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._image_view = pg.ImageView()
        self._image_view.ui.histogram.hide()
        self._image_view.ui.roiBtn.hide()
        self._image_view.ui.menuBtn.hide()
        layout.addWidget(self._image_view)

        bar = QHBoxLayout()
        self._start_btn = QPushButton("Start")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._status = QLabel("no camera" if camera is None else "idle")
        bar.addWidget(self._start_btn)
        bar.addWidget(self._stop_btn)
        bar.addWidget(self._status, 1)
        layout.addLayout(bar)

        self._start_btn.clicked.connect(self.start)
        self._stop_btn.clicked.connect(self.stop)
        self.frame_received.connect(self._on_frame_gui)

    def set_camera(self, camera: CameraBase) -> None:
        """Bind (or replace) the camera. Must not be streaming."""
        if self._streaming:
            raise RuntimeError("stop the current stream before changing cameras")
        self._camera = camera
        self._status.setText("idle")

    def start(self) -> None:
        if self._streaming:
            return
        if self._camera is None:
            raise RuntimeError("no camera set; construct with a camera or call set_camera() first")
        self._camera.open()
        self._camera.start_stream(on_frame=self._on_frame_bg)
        self._streaming = True
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status.setText("streaming")

    def stop(self) -> None:
        if not self._streaming:
            return
        assert self._camera is not None
        self._camera.stop_stream()
        self._camera.close()
        self._streaming = False
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status.setText("stopped")

    def closeEvent(self, event) -> None:
        self.stop()
        super().closeEvent(event)

    def _on_frame_bg(self, img: np.ndarray) -> None:
        """Called from the camera's receiver thread. Hand off to GUI via Signal."""
        self.frame_received.emit(img)

    def _on_frame_gui(self, img: np.ndarray) -> None:
        """Called on the GUI thread once the signal is dispatched."""
        self._frames_seen += 1
        if self._frames_seen == 1:
            self._image_view.setImage(img.T, autoLevels=True, autoRange=True)
        else:
            # ImageView.updateImage() only refreshes display state (no image arg).
            # For per-frame data updates that preserve user pan/zoom/levels, push
            # the array through the underlying ImageItem.
            self._image_view.getImageItem().setImage(img.T, autoLevels=False)
        self._status.setText(f"{self._frames_seen} frames · {img.shape[1]}×{img.shape[0]} · {img.dtype}")
