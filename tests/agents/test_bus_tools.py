import asyncio

import pytest

from lightfall.agents.bus import AgentBus
from lightfall.agents import bus_tools
from tests.agents.test_bus import FakeEndpoint


@pytest.fixture()
def bus():
    AgentBus.reset_instance()
    yield AgentBus.get_instance()
    AgentBus.reset_instance()


def _call(tool, args):
    # SDK @tool objects expose .handler as an async callable. asyncio.get_event_loop()
    # raises on this environment's Python (no implicit loop in the main thread), so use
    # asyncio.run() instead -- same convention as tests/claude/test_logs_tool.py.
    return asyncio.run(tool.handler(args))


def test_send_message_tool_delivers(bus, monkeypatch):
    # run_on_main_thread executes inline in tests (no running loop): patch to direct call
    monkeypatch.setattr(bus_tools, "run_on_main_thread", lambda fn, *a, **k: fn(*a, **k))
    ep = FakeEndpoint("lightfall")
    bus.register("lightfall", ep)
    tools = bus_tools._make_tools(lambda: "observer")
    send = next(t for t in tools if t.name == "send_message")
    result = _call(send, {"to": "lightfall", "message": "hello"})
    assert "delivered" in str(result)
    assert ep.received == [("observer", "hello")]


def test_send_message_tool_reports_missing_target(bus, monkeypatch):
    monkeypatch.setattr(bus_tools, "run_on_main_thread", lambda fn, *a, **k: fn(*a, **k))
    tools = bus_tools._make_tools(lambda: "observer")
    send = next(t for t in tools if t.name == "send_message")
    result = _call(send, {"to": "ghost", "message": "hi"})
    assert "error" in str(result)


def test_list_agents_tool(bus, monkeypatch):
    monkeypatch.setattr(bus_tools, "run_on_main_thread", lambda fn, *a, **k: fn(*a, **k))
    bus.register("a", FakeEndpoint("a"))
    tools = bus_tools._make_tools(lambda: "x")
    lst = next(t for t in tools if t.name == "list_agents")
    assert "a" in str(_call(lst, {}))
