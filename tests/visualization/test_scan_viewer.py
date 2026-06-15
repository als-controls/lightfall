"""Tests for the Scan Viewer visualization."""
from __future__ import annotations

from lightfall.visualization.widgets.scan_viewer import ScanViewerVisualization


class _FakeStream:
    def __init__(self, data_keys: dict):
        self.metadata = {"data_keys": data_keys, "hints": {"fields": []}}


class _FakeRun:
    def __init__(self, start: dict, data_keys: dict):
        self.metadata = {"start": start}
        self._streams = {"primary": _FakeStream(data_keys)}

    def __getitem__(self, key):
        return self._streams[key]

    def keys(self):
        return list(self._streams)


def _scan_with_images():
    return _FakeRun(
        start={
            "hints": {"dimensions": [[["motor_y"], "primary"], [["motor_x"], "primary"]]},
            "shape": [4, 5],
        },
        data_keys={
            "motor_y": {"shape": [], "dtype": "number"},
            "motor_x": {"shape": [], "dtype": "number"},
            "det_image": {"shape": [100, 256, 256], "dtype": "array"},
        },
    )


def test_can_handle_scan_with_per_point_images_scores_highest():
    score = ScanViewerVisualization.can_handle(_scan_with_images())
    assert score == 90


def test_can_handle_rejects_scalar_only_scan():
    run = _FakeRun(
        start={"hints": {"dimensions": [[["m"], "primary"]]}, "shape": [10]},
        data_keys={"m": {"shape": [], "dtype": "number"}, "I": {"shape": [], "dtype": "number"}},
    )
    assert ScanViewerVisualization.can_handle(run) == 0


def test_can_handle_rejects_imagestack_without_scan_dims():
    run = _FakeRun(
        start={},  # no dimensions
        data_keys={"det_image": {"shape": [256, 256], "dtype": "array"}},
    )
    assert ScanViewerVisualization.can_handle(run) == 0


def test_image_fields_only(qtbot):
    w = ScanViewerVisualization()
    qtbot.addWidget(w)
    w.set_run(_scan_with_images())
    w.set_stream("primary")
    assert w.get_fields() == ["det_image"]


def test_detect_layout_empty_shape_is_safe(qtbot):
    w = ScanViewerVisualization()
    qtbot.addWidget(w)
    w._data_keys = {"x": {"shape": []}}
    # a fake client with empty shape must not raise
    class _C: shape = (); metadata = {}
    w._detect_layout("x", _C())
    assert w._layout == "empty"
    assert w._n_points == 0


class _FakeArrayClient:
    def __init__(self, shape, frame_per_point=None):
        self.shape = shape
        self.metadata = {}
        if frame_per_point is not None:
            self.metadata["frame_per_point"] = frame_per_point


def test_detect_layout_3d_with_frame_per_point(qtbot):
    run = _scan_with_images()  # data_keys det_image shape [100,256,256], start shape [4,5]
    w = ScanViewerVisualization(); qtbot.addWidget(w)
    w.set_run(run); w._stream = run["primary"]; w._data_keys = run["primary"].metadata["data_keys"]
    client = _FakeArrayClient((360, 256, 256), frame_per_point=10)
    w._detect_layout("det_image", client)
    assert w._layout == "3d"
    assert w._n_frames == 10
    assert w._n_points == 36
    assert w._frame_shape == (256, 256)


def test_detect_layout_4d(qtbot):
    run = _scan_with_images()
    w = ScanViewerVisualization(); qtbot.addWidget(w)
    w.set_run(run); w._stream = run["primary"]; w._data_keys = run["primary"].metadata["data_keys"]
    client = _FakeArrayClient((36, 10, 256, 256))
    w._detect_layout("det_image", client)
    assert w._layout == "4d"
    assert w._n_points == 36
    assert w._n_frames == 10
    assert w._frame_shape == (256, 256)


def test_detect_layout_3d_one_frame_per_point_fallback(qtbot):
    # No frame_per_point metadata, data_keys shape is 2-D -> fpp falls back to 1
    run = _FakeRun(
        start={"hints": {"dimensions": [[["mx"], "primary"], [["my"], "primary"]]}, "num_points": 30},
        data_keys={"mx": {"shape": [], "dtype": "number"},
                   "my": {"shape": [], "dtype": "number"},
                   "det_image": {"shape": [256, 256], "dtype": "array"}},
    )
    w = ScanViewerVisualization(); qtbot.addWidget(w)
    w.set_run(run); w._stream = run["primary"]; w._data_keys = run["primary"].metadata["data_keys"]
    client = _FakeArrayClient((30, 256, 256))  # no fpp metadata; 30 == num_points
    w._detect_layout("det_image", client)
    assert w._layout == "3d"
    assert w._n_frames == 1
    assert w._n_points == 30


# ---------------------------------------------------------------------------
# Task 6: smoke test – map fill + point selection
# ---------------------------------------------------------------------------

import numpy as np


class _FakeArrayClientWithData:
    """In-memory 3-D (n_points*fpp, H, W) client; subcube() does numpy slicing."""

    def __init__(self, array, frame_per_point):
        self._a = array
        self.shape = array.shape
        self.metadata = {"frame_per_point": frame_per_point}

    def subcube(self, slices):
        idx = tuple(
            (sl if isinstance(sl, int)
             else slice(sl[0], sl[1]) if isinstance(sl, tuple)
             else slice(None))
            for sl in slices
        )
        return np.asarray(self._a[idx], dtype=np.float64)


def _grid_run_3d():
    # 6 points (2x3 grid), fpp=4, 8x8 frames; point p's rows all == p
    return _FakeRun(
        start={
            "hints": {"dimensions": [[["motor_y"], "primary"], [["motor_x"], "primary"]]},
            "shape": [2, 3],
            "num_points": 6,
        },
        data_keys={
            "motor_y": {"shape": [], "dtype": "number"},
            "motor_x": {"shape": [], "dtype": "number"},
            "det_image": {"shape": [4, 8, 8], "dtype": "array"},
        },
    )


def test_scan_viewer_fills_map_and_selects_point(qtbot, monkeypatch):
    from lightfall.visualization.widgets import scan_viewer as sv

    fpp, n_points, H, W = 4, 6, 8, 8
    arr = np.zeros((n_points * fpp, H, W), dtype=np.float64)
    for p in range(n_points):
        arr[p * fpp:(p + 1) * fpp] = float(p)
    client = _FakeArrayClientWithData(arr, fpp)

    run = _grid_run_3d()
    # Make the stream resolve det_image to our fake client:
    stream = run["primary"]
    stream.__class__.__getitem__ = lambda self, key: client if key == "det_image" else (_ for _ in ()).throw(KeyError(key))

    monkeypatch.setattr(sv, "fetch_subcube", lambda c, slices: c.subcube(slices))

    w = ScanViewerVisualization()
    qtbot.addWidget(w)
    w.set_run(run)
    with qtbot.waitSignal(w._engine.finished, timeout=5000):
        w.set_stream("primary")

    assert w._layout == "3d"
    assert w._n_points == 6 and w._n_frames == 4
    # Mean reduction of point p's sub-cube == p
    assert w._point_values[0] == 0.0
    assert w._point_values[5] == 5.0

    # Selecting a point loads its frames on the right without error
    w.select_point(2)
    assert w._selected_point == 2
