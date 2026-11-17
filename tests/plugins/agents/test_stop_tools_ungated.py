"""Stopping is never gated: the assistant can pause, abort and stop devices in a
session without DEVICE_CONTROL (see auth.engine_guard). Resume stays gated."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import lightfall.plugins.agents.engine_tools as et
from lightfall.plugins.agents.device_tools import DeviceToolsAgent


class _NoPermissions:
    def check_permission(self, _permission):
        return False


class _Engine:
    state_name = "running"

    def __init__(self):
        self.calls: list[str] = []

    def pause(self, defer=True):
        self.calls.append(f"pause:{defer}")

    def resume(self):
        self.calls.append("resume")

    def abort(self, reason=""):
        self.calls.append(f"abort:{reason}")
        return True


def _tools(agent):
    tools = agent.create_tools()
    if not tools:
        pytest.skip("agent tooling not available")
    return {t.name: t for t in tools}


def _call(tool, args):
    result = asyncio.run(tool.handler(args))
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def engine(monkeypatch):
    import lightfall.acquire.engine as engine_pkg

    eng = _Engine()
    monkeypatch.setattr(engine_pkg, "get_engine", lambda: eng)
    return eng


@pytest.fixture
def engine_tools(monkeypatch):
    agent = et.EngineToolsAgent()
    monkeypatch.setattr(agent, "_get_session_manager", lambda: _NoPermissions())
    return _tools(agent)


def test_pause_works_without_device_control(engine, engine_tools):
    payload = _call(engine_tools["lightfall_pause_plan"], {"defer": True})
    assert payload["success"] is True
    assert engine.calls == ["pause:True"]


def test_abort_works_without_device_control(engine, engine_tools):
    payload = _call(engine_tools["lightfall_abort_plan"], {"reason": "beam dump"})
    assert payload["success"] is True
    assert engine.calls == ["abort:beam dump"]


def test_resume_still_needs_device_control(engine, engine_tools):
    payload = _call(engine_tools["lightfall_resume_plan"], {})
    assert payload["success"] is False
    assert "DEVICE_CONTROL" in payload["error"]
    assert engine.calls == []


def test_stop_device_works_without_device_control(monkeypatch):
    stopped = []
    device = SimpleNamespace(ophyd_device=SimpleNamespace(stop=lambda: stopped.append(1)))
    catalog = SimpleNamespace(is_connected=True, get_device_by_name=lambda name: device)
    agent = DeviceToolsAgent()
    monkeypatch.setattr(agent, "_get_session_manager", lambda: _NoPermissions())
    monkeypatch.setattr(agent, "_get_catalog", lambda: catalog)
    payload = _call(_tools(agent)["lightfall_stop_device"], {"name": "samx"})
    assert payload["success"] is True
    assert stopped == [1]


def test_move_motor_still_needs_device_control(monkeypatch):
    agent = DeviceToolsAgent()
    monkeypatch.setattr(agent, "_get_session_manager", lambda: _NoPermissions())
    payload = _call(_tools(agent)["lightfall_move_motor"], {"name": "samx", "position": 1.0})
    assert payload["success"] is False
    assert "DEVICE_CONTROL" in payload["error"]
