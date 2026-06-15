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
    class _C: shape = ()
    w._detect_layout("x", _C())
    assert w._n_points == 0
