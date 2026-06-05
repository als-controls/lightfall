"""Regression test: a failed frame fetch must not crash the open-run action.

Reproduces the PI_MTE3 incident — opening a run in the Visualization panel
when the Tiled storage host is down. The backing array advertises a shape
(so ``set_field`` happily asks for the last frame), but every per-frame fetch
500s. Previously the ``HTTPStatusError`` propagated through
``setCurrentIndex`` → ``set_field`` → ``open_run`` → the double-click slot and
escalated to an unhandled-exception crash. It must now be caught and surfaced.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import numpy as np
import pytest


class FakeArray:
    """Fake Tiled ArrayClient exposing only ``.shape`` (fetch is patched)."""

    def __init__(self, shape: tuple[int, ...]):
        self._shape = shape

    @property
    def shape(self):
        return self._shape


class FakeContainer:
    def __init__(self, children=None, metadata=None):
        self._children = children or {}
        self._metadata = metadata or {}

    def __contains__(self, key):
        return key in self._children

    def __getitem__(self, key):
        return self._children[key]

    def __iter__(self):
        return iter(self._children)

    def keys(self):
        return list(self._children.keys())

    @property
    def metadata(self):
        return self._metadata


def _make_image_run(n_frames: int = 4, hw: int = 8) -> FakeContainer:
    """A run whose primary stream has a 3D image field (N, H, W)."""
    primary = FakeContainer(
        children={"PI_MTE3_image": FakeArray((n_frames, hw, hw))},
        metadata={
            "data_keys": {"PI_MTE3_image": {"shape": [n_frames, hw, hw]}},
            "hints": {"fields": ["PI_MTE3_image"]},
        },
    )
    return FakeContainer(children={"primary": primary})


def test_open_run_survives_frame_fetch_500(qtbot):
    from lightfall.visualization.widgets.image_stack import (
        ImageStackVisualization,
    )

    widget = ImageStackVisualization()
    qtbot.addWidget(widget)

    failures: list[tuple[int, str]] = []
    widget._image_view.frameLoadFailed.connect(
        lambda idx, msg: failures.append((idx, msg))
    )

    run = _make_image_run(n_frames=4)
    widget.set_run(run)

    # Every frame fetch fails, exactly like a 500 from a down storage host.
    def boom(_client: Any, _index: int) -> np.ndarray:
        raise RuntimeError("Server error '500 Internal Server Error'")

    with patch(
        "lightfall.utils.tiled_helpers.fetch_frame", side_effect=boom
    ):
        # The bug: this raised and crashed the app. It must now return cleanly.
        widget.set_stream("primary")

    # Failure was caught and signalled (not propagated).
    assert failures, "frameLoadFailed should have been emitted"
    assert failures[0][0] == 3, "set_field opens the last frame first"

    # The user sees an explanatory status message instead of a crash.
    assert widget._frame_error is not None
    assert "unavailable" in widget._frame_error.lower()
