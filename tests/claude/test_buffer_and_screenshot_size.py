"""Regression tests for the 1 MiB SDK buffer overflow (large screenshot tool_result).

A full-window PNG base64 payload exceeded the SDK transport's default
max_buffer_size (1 MiB), killing the turn with
"JSON message exceeded maximum buffer size". Two-layer fix:
options raise max_buffer_size, and screenshots downscale before encoding.
"""

from __future__ import annotations

import base64

import pytest
from PySide6.QtWidgets import QLabel

from lightfall.claude.agent import SDK_MAX_BUFFER_SIZE
from lightfall.claude.tools.screenshot import MAX_SCREENSHOT_DIM, capture_screenshot_b64


def test_sdk_buffer_size_constant_is_generous():
    # 16 MiB headroom: covers a downscaled PNG many times over while still
    # bounding runaway messages.
    assert SDK_MAX_BUFFER_SIZE >= 16 * 1024 * 1024


def test_agent_options_carry_max_buffer_size(qapp, monkeypatch):
    from lightfall.claude import agent as agent_mod

    captured = {}

    class FakeClient:
        def __init__(self, options):
            captured["options"] = options

    monkeypatch.setattr(agent_mod, "ClaudeSDKClient",
                        lambda options: FakeClient(options))
    label = QLabel()
    agent = agent_mod.QtClaudeAgent(target_window=label)
    assert captured["options"].max_buffer_size == SDK_MAX_BUFFER_SIZE


@pytest.mark.parametrize("w,h", [(4000, 2200), (800, 600)])
def test_capture_downscales_to_max_dim(qapp, w, h):
    label = QLabel("x")
    label.resize(w, h)
    data = capture_screenshot_b64(label)
    assert data is not None
    png = base64.b64decode(data)
    # PNG IHDR: width/height are bytes 16-24 big-endian
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    assert max(width, height) <= MAX_SCREENSHOT_DIM
    dpr = label.devicePixelRatio()
    if max(w * dpr, h * dpr) <= MAX_SCREENSHOT_DIM:
        # small windows keep their native (DPR-scaled) resolution
        assert (width, height) == (round(w * dpr), round(h * dpr))
