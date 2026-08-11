"""End-to-end deterministic chain test: observation -> advisor batch ->
severity gate -> AgentBus -> ClaudeSessionEndpoint policy -> prompt text.

No LLM, no engine: MonitorService's scheduler is stubbed (as in
tests/monitor/test_service_forwarding.py) and the advisor is a plain sync
stub. AgentBus and ClaudeSessionEndpoint are the real, unmodified
implementations.
"""

import pytest

from lightfall.agents.bus import AgentBus
from lightfall.claude.bus_endpoint import ClaudeSessionEndpoint, format_bus_prompt
from lightfall.monitor.models import Observation
from lightfall.monitor.service import MonitorService


def _obs(severity="warn"):
    return Observation(severity=severity, feed_name="acquisition_health", run_uid="u",
                       title="t", message="m", state_key=f"k:{severity}")


@pytest.fixture()
def service(monkeypatch):
    MonitorService.reset_instance()
    monkeypatch.setattr(MonitorService, "_build_scheduler", lambda self: None)
    AgentBus.reset_instance()
    svc = MonitorService.get_instance()
    yield svc
    MonitorService.reset_instance()
    AgentBus.reset_instance()


def _prime_observer_spec(monkeypatch, floor="warn"):
    from lightfall.agents.registry import AgentSpecRegistry
    from lightfall.agents.spec import AgentSpec
    from pathlib import Path
    spec = AgentSpec(name="observer", description="d", prompt="p", scope="core",
                     source_path=Path("observer.md"), forward_min_severity=floor)
    monkeypatch.setattr(AgentSpecRegistry.get_instance(), "get",
                        lambda name: spec if name == "observer" else None)


def _make_endpoint(policy, busy=False):
    submitted: list[str] = []
    ep = ClaudeSessionEndpoint(
        "lightfall", "main agent",
        submit=lambda text: submitted.append(text) or True,
        is_busy=lambda: busy,
        on_queued=lambda s, m: None,
        policy=policy,
    )
    return ep, submitted


def test_chain_queue_policy_queues_then_flushes_on_respond(service, monkeypatch, qtbot):
    _prime_observer_spec(monkeypatch, floor="warn")
    ep, submitted = _make_endpoint("queue")
    AgentBus.get_instance().register("lightfall", ep)

    service._advisor_enabled = lambda: True
    service._advise_async = False
    service.set_advisor(type("A", (), {"advise": lambda self, batch: "shutter looks stuck"})())

    # Feed a warn observation through the real entry point.
    service._on_observation(_obs("warn"))
    assert service._advisor_batch  # queued for the debounce flush

    # Fire the debounce flush directly (no LLM, no timer wait needed).
    service._flush_advisor()

    assert ep.pending() == [
        ("observer", "Proactive monitor summary (warn): shutter looks stuck"),
    ]
    assert submitted == []  # queue policy: nothing submitted yet

    # "Respond" action flushes the queued message into the session.
    flushed = ep.flush_pending()
    assert flushed == 1
    assert submitted == [
        format_bus_prompt("observer", "Proactive monitor summary (warn): shutter looks stuck"),
    ]
    assert ep.pending() == []


def test_chain_auto_policy_idle_submits_directly(service, monkeypatch, qtbot):
    _prime_observer_spec(monkeypatch, floor="warn")
    ep, submitted = _make_endpoint("auto", busy=False)
    AgentBus.get_instance().register("lightfall", ep)

    service._advisor_enabled = lambda: True
    service._advise_async = False
    service.set_advisor(type("A", (), {"advise": lambda self, batch: "shutter looks stuck"})())

    service._on_observation(_obs("warn"))
    service._flush_advisor()

    assert submitted == [
        format_bus_prompt("observer", "Proactive monitor summary (warn): shutter looks stuck"),
    ]
    assert ep.pending() == []
