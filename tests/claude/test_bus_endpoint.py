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


def test_auto_idle_submit_refused_queues_instead_of_dropping():
    """Race window: is_busy() said idle, but submit refuses (e.g. widget's
    own _is_busy flipped True in between). Must queue, not silently drop."""
    ep = ClaudeSessionEndpoint(
        "lightfall", "main agent",
        submit=lambda text: False,
        is_busy=lambda: False,
        on_queued=lambda s, m: None,
        policy="auto",
    )
    assert ep.deliver("observer", "x") == "queued"
    assert ep.pending() == [("observer", "x")]


def test_flush_pending_requeues_remainder_on_refusal():
    submitted = []
    results = iter([True, False])
    ep = ClaudeSessionEndpoint(
        "lightfall", "main agent",
        submit=lambda text: (submitted.append(text) or next(results)),
        is_busy=lambda: True,
        on_queued=lambda s, m: None,
        policy="queue",
    )
    ep.deliver("a", "1")
    ep.deliver("b", "2")
    ep.deliver("c", "3")
    assert ep.flush_pending() == 1
    assert len(submitted) == 2
    assert ep.pending() == [("b", "2"), ("c", "3")]


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


def test_message_queued_signal_emitted(qtbot):
    ep, submitted, _ = _make(policy="queue")
    with qtbot.waitSignal(ep.message_queued, timeout=1000) as blocker:
        ep.deliver("observer", "hi")
    assert blocker.args == ["observer", "hi"]


def test_accept_single_message():
    ep, submitted, _ = _make(policy="queue")
    ep.deliver("a", "1")
    ep.deliver("b", "2")
    assert ep.accept(0) is True
    assert len(submitted) == 1 and "1" in submitted[0]
    assert ep.pending() == [("b", "2")]


def test_accept_refused_submit_keeps_message():
    ep, submitted, _ = _make(policy="queue")
    ep._submit = lambda text: False
    ep.deliver("a", "1")
    assert ep.accept(0) is False
    assert ep.pending() == [("a", "1")]


def test_dismiss_removes_without_submit():
    ep, submitted, _ = _make(policy="queue")
    ep.deliver("a", "1")
    assert ep.dismiss(0) == ("a", "1")
    assert ep.pending() == [] and submitted == []
    assert ep.dismiss(5) is None


def test_auto_accept_signal(qtbot):
    ep, submitted, _ = _make(policy="auto")
    with qtbot.waitSignal(ep.message_auto_accepted, timeout=1000) as blocker:
        ep.deliver("observer", "x")
    assert blocker.args == ["observer", "x"]


def test_whitelisted_sender_bypasses_queue_policy():
    ep, submitted, queued = _make(policy="queue")
    ep.always_accept("observer")
    assert ep.deliver("observer", "x") == "delivered"
    assert submitted and "x" in submitted[0]
    assert queued == []


def test_whitelisted_sender_busy_queues_then_flush_whitelisted():
    ep, submitted, _ = _make(policy="queue", busy=True)
    ep.always_accept("observer")
    assert ep.deliver("observer", "x") == "queued"
    ep._is_busy = lambda: False
    assert ep.flush_whitelisted() == 1
    assert submitted


def test_always_accept_drains_existing_pending_from_sender_only():
    ep, submitted, _ = _make(policy="queue")
    ep.deliver("observer", "1")
    ep.deliver("saxs", "2")
    ep.deliver("observer", "3")
    assert ep.always_accept("observer") == 2
    assert len(submitted) == 2
    assert ep.pending() == [("saxs", "2")]
    assert ep.auto_accepts("observer") and not ep.auto_accepts("saxs")


def test_constructor_seeded_whitelist():
    ep, submitted, _ = _make(policy="queue")
    ep2 = ClaudeSessionEndpoint(
        "dev", "d", submit=lambda t: submitted.append(t) or True,
        is_busy=lambda: False, on_queued=lambda s, m: None,
        policy="queue", auto_accept_senders={"lightfall"})
    assert ep2.deliver("lightfall", "hi") == "delivered"
