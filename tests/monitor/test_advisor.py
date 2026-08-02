# tests/monitor/test_advisor.py
import asyncio

import pytest

from lightfall.monitor.advisor import MonitorAdvisor, collect_reply, format_advisor_prompt
from lightfall.monitor.models import Observation


def _obs(title, sev="warn"):
    return Observation(severity=sev, feed_name="f", run_uid="u",
                       title=title, message="m", state_key=f"f:{title}",
                       metrics={"x": 1.0}, recommendation="do y")


def test_format_prompt_includes_each_observation():
    p = format_advisor_prompt([_obs("A"), _obs("B")])
    assert "A" in p and "B" in p and "do y" in p


def test_advise_returns_empty_for_no_observations():
    adv = MonitorAdvisor(query_fn=lambda prompt: "should not be called")
    assert adv.advise([]) == ""


def test_advise_calls_query_fn_with_prompt():
    seen = {}
    adv = MonitorAdvisor(query_fn=lambda prompt: seen.update({"p": prompt}) or "FUSED")
    out = adv.advise([_obs("A")])
    assert out == "FUSED"
    assert "A" in seen["p"]


class _FakeBlock:
    def __init__(self, text): self.text = text


class _FakeAssistant:
    def __init__(self, blocks): self.content = blocks


class _FakeResult:
    pass


class _FakeClient:
    def __init__(self, msgs): self._msgs = msgs
    async def query(self, prompt): self._prompt = prompt
    async def receive_response(self):
        for m in self._msgs:
            yield m


def test_sdk_query_uses_observer_spec_when_registered(monkeypatch, tmp_path):
    from lightfall.agents.registry import AgentSpecRegistry
    from lightfall.agents.spec import AgentSpec

    AgentSpecRegistry.reset_instance()
    reg = AgentSpecRegistry.get_instance()
    spec = AgentSpec(
        name="observer",
        description="d",
        prompt="You watch {{beamline}}.",
        scope="core",
        source_path=tmp_path / "observer.md",
    )
    reg._specs["observer"] = spec

    monkeypatch.setattr(
        "lightfall.agents.skills_store.template_variables",
        lambda: {"beamline": "8.3.1", "user": "u", "endstation": ""},
    )

    seen_opts = {}

    class _FakeOptions:
        def __init__(self, **kwargs):
            seen_opts.update(kwargs)

    class _FakeClient:
        def __init__(self, options): pass
        async def connect(self): pass
        async def disconnect(self): pass

    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeAgentOptions", _FakeOptions, raising=False
    )
    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeSDKClient", _FakeClient, raising=False
    )
    monkeypatch.setattr(
        "lightfall.monitor.advisor.collect_reply",
        lambda client, prompt: _async_return("ok"),
    )

    adv = MonitorAdvisor()
    out = adv._sdk_query("hi")
    assert out == "ok"
    assert seen_opts["system_prompt"] == "You watch 8.3.1."

    AgentSpecRegistry.reset_instance()


def test_sdk_query_falls_back_when_no_observer_spec(monkeypatch, tmp_path):
    from lightfall.agents.registry import AgentSpecRegistry

    AgentSpecRegistry.reset_instance()
    AgentSpecRegistry.get_instance()  # empty registry, no "observer"

    seen_opts = {}

    class _FakeOptions:
        def __init__(self, **kwargs):
            seen_opts.update(kwargs)

    class _FakeClient:
        def __init__(self, options): pass
        async def connect(self): pass
        async def disconnect(self): pass

    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeAgentOptions", _FakeOptions, raising=False
    )
    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeSDKClient", _FakeClient, raising=False
    )
    monkeypatch.setattr(
        "lightfall.monitor.advisor.collect_reply",
        lambda client, prompt: _async_return("ok"),
    )

    adv = MonitorAdvisor()
    out = adv._sdk_query("hi")
    assert out == "ok"
    from lightfall.monitor.advisor import ADVISOR_SYSTEM_PROMPT
    assert seen_opts["system_prompt"] == ADVISOR_SYSTEM_PROMPT

    AgentSpecRegistry.reset_instance()


async def _async_return(value):
    return value


def test_collect_reply_joins_textblocks_until_result(monkeypatch):
    # Patch the SDK type-checks collect_reply uses to our fakes.
    import lightfall.monitor.advisor as mod
    monkeypatch.setattr(mod, "_AssistantMessage", _FakeAssistant)
    monkeypatch.setattr(mod, "_TextBlock", _FakeBlock)
    monkeypatch.setattr(mod, "_ResultMessage", _FakeResult)
    client = _FakeClient([_FakeAssistant([_FakeBlock("Hello "), _FakeBlock("world")]),
                          _FakeResult()])
    loop = asyncio.new_event_loop()
    try:
        out = loop.run_until_complete(collect_reply(client, "p"))
    finally:
        loop.close()
    assert out == "Hello world"
