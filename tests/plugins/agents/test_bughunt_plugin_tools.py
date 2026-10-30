"""Regression tests for the 2026-10 bug hunt: agent tool plugins."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from lightfall.acquire.plans.user_plans import UserPlanService
from lightfall.devices.model import DeviceCategory
from lightfall.plugins.agents.device_tools import DeviceToolsAgent
from lightfall.plugins.agents.plan_tools import PlanToolsAgent
from lightfall.utils.git_tracker import GitTracker


@pytest.fixture
def plans_dir(tmp_path, monkeypatch):
    UserPlanService.reset_instance()
    GitTracker.reset_instance()
    repo_root = tmp_path / "lightfall"
    plans = repo_root / "plans"
    plans.mkdir(parents=True)
    monkeypatch.setattr(GitTracker, "_instance", GitTracker(repo_root=repo_root))
    service = UserPlanService.get_instance()
    monkeypatch.setattr(service, "_plans_dir", plans)
    yield plans
    UserPlanService.reset_instance()
    GitTracker.reset_instance()


def _tool(agent, name):
    return next(t for t in agent.create_tools() if getattr(t, "name", None) == name)


def _body(result):
    assert "content" in result, f"not an MCP envelope: {result!r}"
    return json.loads(result["content"][0]["text"])


@pytest.mark.parametrize("tool_name", ["lightfall_get_user_plan", "lightfall_delete_user_plan"])
def test_plan_tools_refuse_path_traversal(plans_dir, tool_name):
    """Plan names become file names: '../x' must be refused, not resolved."""
    outside = plans_dir.parent / "secret.py"
    outside.write_text("# not a plan\n", encoding="utf-8")

    result = asyncio.run(_tool(PlanToolsAgent(), tool_name).handler({"name": "../secret"}))

    body = _body(result)
    assert body["success"] is False and "Invalid plan name" in body["error"]
    assert outside.exists()


def test_create_user_plan_returns_mcp_envelope(plans_dir):
    result = asyncio.run(_tool(PlanToolsAgent(), "lightfall_create_user_plan").handler({
        "name": "not valid",
        "code": "def plan():\n    yield None\n",
        "description": "bad name",
    }))
    body = _body(result)
    assert body["success"] is False
    assert result.get("is_error") in (True, None) or result.get("isError") in (True, None)


def _device(name, category, beamline, active=True):
    d = MagicMock()
    d.id = name
    d.name = name
    d.description = ""
    d.category = category
    d.prefix = f"{name}:"
    d.beamline = beamline
    d.active = active
    d.state = None
    return d


def test_list_devices_applies_filters_to_search_results(qapp, monkeypatch):
    hits = [
        _device("m1", DeviceCategory.MOTOR, "7.0.1.1"),
        _device("d1", DeviceCategory.DETECTOR, "7.0.1.1"),
        _device("m2", DeviceCategory.MOTOR, "4.0.2", active=False),
    ]
    catalog = MagicMock()
    catalog.is_connected = True
    catalog.search_devices.return_value = hits
    agent = DeviceToolsAgent()
    monkeypatch.setattr(agent, "_get_catalog", lambda: catalog)
    tool = _tool(agent, "lightfall_list_devices")

    body = _body(asyncio.run(tool.handler({"query": "m", "category": "motor"})))
    assert [d["name"] for d in body["devices"]] == ["m1"], body

    body = _body(asyncio.run(tool.handler({"query": "m", "active_only": False})))
    assert sorted(d["name"] for d in body["devices"]) == ["d1", "m1", "m2"]

    body = _body(asyncio.run(tool.handler({"query": "m", "beamline": "4.0.2", "active_only": False})))
    assert [d["name"] for d in body["devices"]] == ["m2"]
