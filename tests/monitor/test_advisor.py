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
