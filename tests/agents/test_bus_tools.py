import asyncio
from pathlib import Path

import pytest

from lightfall.agents.bus import AgentBus
from lightfall.agents.registry import AgentSpecRegistry
from lightfall.agents.spec import AgentSpec
from lightfall.agents import bus_tools
from tests.agents.test_bus import FakeEndpoint


def _spec(name, **kw):
    defaults = dict(description=f"d-{name}", prompt="p", scope="core",
                     source_path=Path(f"{name}.md"))
    defaults.update(kw)
    return AgentSpec(name=name, **defaults)


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


def test_list_agents_tool_includes_note(bus, monkeypatch):
    monkeypatch.setattr(bus_tools, "run_on_main_thread", lambda fn, *a, **k: fn(*a, **k))
    tools = bus_tools._make_tools(lambda: "x")
    lst = next(t for t in tools if t.name == "list_agents")
    assert bus_tools.LIST_AGENTS_NOTE in str(_call(lst, {}))


@pytest.fixture()
def registry():
    AgentSpecRegistry.reset_instance()
    yield AgentSpecRegistry.get_instance()
    AgentSpecRegistry.reset_instance()


def test_agent_roster_merges_defined_and_running(bus, registry, monkeypatch):
    specs = [
        _spec("running_spec"),
        _spec("idle_spec"),
        _spec("suffixed_spec"),
    ]
    monkeypatch.setattr(registry, "enabled_specs", lambda: specs)
    bus.register("running_spec", FakeEndpoint("running_spec"))
    # collide with an already-running "suffixed_spec" so the bus assigns #2
    bus.register("suffixed_spec", FakeEndpoint("suffixed_spec"))
    bus.register("suffixed_spec", FakeEndpoint("suffixed_spec"))
    bus.register("legacy_runtime_only", FakeEndpoint("legacy_runtime_only"))

    roster = bus_tools.agent_roster()
    by_name = {r["name"]: r for r in roster if r["scope"] != "runtime"}

    assert by_name["running_spec"]["running"] is True
    assert by_name["idle_spec"]["running"] is False
    assert by_name["suffixed_spec"]["running"] is True

    runtime_entries = [r for r in roster if r["scope"] == "runtime"]
    assert any(r["name"] == "suffixed_spec#2" for r in runtime_entries)
    assert any(r["name"] == "legacy_runtime_only" for r in runtime_entries)
    for r in runtime_entries:
        assert r["openable"] is False
        assert r["subagent_eligible"] is False


def test_launch_agent_tool_calls_launcher_with_sender_identity(bus, monkeypatch):
    monkeypatch.setattr(bus_tools, "run_on_main_thread", lambda fn, *a, **k: fn(*a, **k))
    calls = []

    def launcher(name, launched_by, first_message):
        calls.append((name, launched_by, first_message))
        return {"success": True, "agent": name, "focused_existing": False}

    bus.set_launcher(launcher)
    tools = bus_tools._make_tools(lambda: "observer")
    launch = next(t for t in tools if t.name == "launch_agent")
    result = _call(launch, {"name": "saxs", "first_message": "hi"})
    assert calls == [("saxs", "observer", "hi")]
    assert "success" in str(result)


def test_launch_agent_tool_defaults_first_message_to_none(bus, monkeypatch):
    monkeypatch.setattr(bus_tools, "run_on_main_thread", lambda fn, *a, **k: fn(*a, **k))
    calls = []

    def launcher(name, launched_by, first_message):
        calls.append((name, launched_by, first_message))
        return {"success": True, "agent": name}

    bus.set_launcher(launcher)
    tools = bus_tools._make_tools(lambda: "observer")
    launch = next(t for t in tools if t.name == "launch_agent")
    _call(launch, {"name": "saxs"})
    assert calls == [("saxs", "observer", None)]


def test_launch_agent_tool_no_launcher_returns_clean_error(bus, monkeypatch):
    monkeypatch.setattr(bus_tools, "run_on_main_thread", lambda fn, *a, **k: fn(*a, **k))
    tools = bus_tools._make_tools(lambda: "observer")
    launch = next(t for t in tools if t.name == "launch_agent")
    result = _call(launch, {"name": "saxs"})
    assert "no launcher available" in str(result)


def test_launch_agent_is_allowed_tool():
    assert "mcp__bus__launch_agent" in bus_tools.BUS_ALLOWED_TOOLS


def test_agent_roster_falls_back_when_registry_fails(bus, registry, monkeypatch):
    def _boom():
        raise RuntimeError("registry broke")

    monkeypatch.setattr(registry, "enabled_specs", _boom)
    bus.register("a", FakeEndpoint("a"))

    roster = bus_tools.agent_roster()
    assert roster == [
        {
            "name": "a",
            "description": "d-a",
            "scope": "runtime",
            "running": True,
            "openable": False,
            "subagent_eligible": False,
        }
    ]
