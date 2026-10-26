"""Dry-run plan preview: nothing moves, and the summary catches what reviewers look for."""

from __future__ import annotations

import asyncio
import json

import bluesky.plan_stubs as bps
import bluesky.plans as bp
import pytest
from ophyd import SoftPositioner
from ophyd.sim import SynAxis, SynGauss

from lightfall.acquire.plans.preview import preview_plan


@pytest.fixture
def motor():
    return SoftPositioner(name="sx", limits=(-10, 10), init_pos=1.0)


@pytest.fixture
def det(motor):
    axis = SynAxis(name="ax")
    return SynGauss("det", axis, "ax", center=0, Imax=1)


def test_preview_does_not_move_the_motor(motor, det):
    out = preview_plan(bp.scan([det], motor, -5, 5, 11)).to_dict()
    assert motor.position == 1.0
    assert out["ok"] is True
    assert out["events"] == 11
    assert out["runs"] == {"opened": 1, "closed": 1}
    assert out["detectors"] == ["det"]
    (move,) = out["moves"]
    assert move["device"] == "sx"
    assert move["target_range"] == [-5.0, 5.0]
    assert move["start_position"] == 1.0
    assert move["limits"] == [-10.0, 10.0]


def test_relative_plan_previews_against_real_position(motor, det):
    out = preview_plan(bp.rel_scan([det], motor, -1, 1, 3)).to_dict()
    (move,) = out["moves"]
    assert move["target_range"] == [0.0, 2.0]
    assert move["ends_at_start"] is True
    assert not any("left at" in w for w in out["warnings"])


def test_limit_violation_is_reported(motor, det):
    out = preview_plan(bp.scan([det], motor, -20, 0, 3)).to_dict()
    assert out["ok"] is False
    assert out["moves"][0]["violation_count"] == 1
    assert any("rejected by limits" in w for w in out["warnings"])


def test_large_move_and_end_state_warnings(motor, det):
    warnings = preview_plan(bp.scan([det], motor, 8, 9, 2)).to_dict()["warnings"]
    assert any("of its travel range" in w for w in warnings)
    assert any("left at 9" in w for w in warnings)


def test_sensitive_device_warning(det):
    shutter = SoftPositioner(name="fe_shutter", init_pos=0)

    def plan():
        yield from bps.mv(shutter, 1)
        yield from bp.count([det])

    warnings = preview_plan(plan()).to_dict()["warnings"]
    assert any("fe_shutter looks like a shutter" in w for w in warnings)


def test_run_without_detector_or_events(motor):
    def plan():
        yield from bps.open_run()
        yield from bps.mv(motor, 2)
        yield from bps.close_run()

    warnings = preview_plan(plan()).to_dict()["warnings"]
    assert any("never triggers a detector" in w for w in warnings)
    assert any("records no events" in w for w in warnings)


def test_plan_that_raises_keeps_partial_summary(motor):
    def plan():
        yield from bps.mv(motor, 3)
        raise ValueError("bad idea")

    out = preview_plan(plan()).to_dict()
    assert out["ok"] is False
    assert out["error"] == "ValueError: bad idea"
    assert out["moves"][0]["last_target"] == 3


def test_unbounded_plan_is_truncated(motor):
    def plan():
        while True:
            yield from bps.null()

    out = preview_plan(plan(), max_messages=50).to_dict()
    assert out["truncated"] is True
    assert out["messages"] == 50


def test_sleep_counts_toward_duration(det):
    def plan():
        yield from bps.sleep(1.5)
        yield from bp.count([det])
        yield from bps.sleep(2.5)

    out = preview_plan(plan()).to_dict()
    assert out["duration_breakdown_s"]["sleep"] == pytest.approx(4.0)


class _Catalog:
    def __init__(self, devices):
        self._devices = devices

    def get_all_ophyd_devices(self):
        return self._devices


def _preview_tool(monkeypatch, devices):
    from lightfall.plugins.agents.plan_tools import PlanToolsAgent

    agent = PlanToolsAgent()
    monkeypatch.setattr(agent, "_get_catalog", lambda: _Catalog(devices))
    return next(t for t in agent.create_tools() if t.name == "lightfall_preview_plan")


def test_preview_tool_runs_code_against_catalog_devices(monkeypatch, motor, det):
    tool = _preview_tool(monkeypatch, {"sx": motor, "det": det})
    result = asyncio.run(tool.handler({"code": "yield from bp.scan([det], sx, 0, 1, 2)"}))
    out = json.loads(result["content"][0]["text"])
    assert out["events"] == 2 and out["moves"][0]["device"] == "sx"
    assert motor.position == 1.0


def test_preview_tool_needs_exactly_one_source(monkeypatch):
    tool = _preview_tool(monkeypatch, {})
    for args in ({}, {"code": "yield from bps.null()", "plan_name": "count"}):
        assert asyncio.run(tool.handler(args))["is_error"] is True


def test_preview_tool_reports_syntax_errors(monkeypatch):
    tool = _preview_tool(monkeypatch, {})
    result = asyncio.run(tool.handler({"code": "yield from ("}))
    assert result["is_error"] is True
    assert "Syntax error" in result["content"][0]["text"]
