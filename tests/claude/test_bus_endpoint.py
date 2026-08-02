from __future__ import annotations

from lightfall.claude.bus_endpoint import ClaudeSessionEndpoint, format_bus_prompt


def _make(policy="queue", busy=False):
    submitted, queued_events = [], []
    ep = ClaudeSessionEndpoint(
        "lightfall", "main agent",
        submit=lambda text: submitted.append(text) or True,
        is_busy=lambda: busy,
        on_queued=lambda s, m: queued_events.append((s, m)),
        policy=policy,
    )
    return ep, submitted, queued_events


def test_format_bus_prompt():
    assert format_bus_prompt("observer", "hi") == "[Message from agent 'observer']\nhi"


def test_queue_policy_queues_and_notifies():
    ep, submitted, queued = _make(policy="queue")
    assert ep.deliver("observer", "beam soft") == "queued"
    assert submitted == []
    assert queued == [("observer", "beam soft")]
    assert ep.pending() == [("observer", "beam soft")]


def test_auto_policy_idle_submits_immediately():
    ep, submitted, _ = _make(policy="auto")
    assert ep.deliver("observer", "x") == "delivered"
    assert submitted == [format_bus_prompt("observer", "x")]
    assert ep.pending() == []


def test_auto_policy_busy_queues_then_flushes():
    ep, submitted, _ = _make(policy="auto", busy=True)
    assert ep.deliver("observer", "x") == "queued"
    assert submitted == []
    assert ep.flush_pending() == 1
    assert submitted == [format_bus_prompt("observer", "x")]


def test_respond_flush_on_queue_policy():
    ep, submitted, _ = _make(policy="queue")
    ep.deliver("a", "1")
    ep.deliver("b", "2")
    assert ep.flush_pending() == 2
    assert len(submitted) == 2


def test_set_policy_validates():
    ep, _, _ = _make()
    ep.set_policy("auto")
    assert ep.policy == "auto"
    import pytest
    with pytest.raises(ValueError):
        ep.set_policy("shout")


def test_wiring_auto_busy_then_completion_flush():
    """Mirrors the widget wiring: deliver while busy queues; query_completed
    flushing (policy == 'auto') submits it."""
    busy = {"v": True}
    submitted = []
    ep = ClaudeSessionEndpoint(
        "lightfall", "main agent",
        submit=lambda text: submitted.append(text) or True,
        is_busy=lambda: busy["v"],
        on_queued=lambda s, m: None,
        policy="auto",
    )
    assert ep.deliver("observer", "beam soft") == "queued"
    assert submitted == []
    # Query completes; widget's query_completed handler flushes when policy == "auto".
    busy["v"] = False
    if ep.policy == "auto":
        ep.flush_pending()
    assert submitted == [format_bus_prompt("observer", "beam soft")]
