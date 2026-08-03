"""BusCardModel — pure-logic bookkeeping for inline Accept/Dismiss agent-
message cards, replacing the phase-2 banner.

Kept Qt-light (no widget construction) so the state machine is testable
without a QApplication.
"""
from __future__ import annotations

import lightfall.claude.bus_endpoint as bus_endpoint_module
from lightfall.claude.bus_endpoint import BusCardModel, ClaudeSessionEndpoint


def test_bus_banner_text_fully_removed():
    """Phase-2 banner helper must be gone -- cards replace it entirely."""
    assert not hasattr(bus_endpoint_module, "bus_banner_text")


def test_add_pending_creates_pending_entry():
    model = BusCardModel()
    entry = model.add_pending("observer", "beam soft")
    assert entry.sender == "observer"
    assert entry.message == "beam soft"
    assert entry.state == "pending"
    assert model.pending_count() == 1


def test_add_auto_creates_auto_entry_not_counted_as_pending():
    model = BusCardModel()
    entry = model.add_auto("observer", "beam soft")
    assert entry.state == "auto"
    assert model.pending_count() == 0


def test_mark_dismissed_removes_from_pending_count():
    model = BusCardModel()
    entry = model.add_pending("a", "1")
    model.mark_dismissed(entry)
    assert entry.state == "dismissed"
    assert model.pending_count() == 0
    # Dismissed entries stay in the model (greyed out), not deleted.
    assert entry in model.entries


def test_remove_deletes_entry_entirely():
    model = BusCardModel()
    entry = model.add_pending("a", "1")
    model.remove(entry)
    assert entry not in model.entries
    assert model.pending_count() == 0


def test_entry_for_pending_index_matches_add_order():
    model = BusCardModel()
    a = model.add_pending("a", "1")
    b = model.add_pending("b", "2")
    c = model.add_pending("c", "3")
    assert model.entry_for_pending_index(0) is a
    assert model.entry_for_pending_index(1) is b
    assert model.entry_for_pending_index(2) is c
    assert model.entry_for_pending_index(3) is None


def test_pending_index_of_after_mid_list_accept():
    """Accepting/removing a middle entry must shift later entries' pending
    indices down by one -- mirrors ClaudeSessionEndpoint._pending mutation."""
    model = BusCardModel()
    a = model.add_pending("a", "1")
    b = model.add_pending("b", "2")
    c = model.add_pending("c", "3")

    assert model.pending_index_of(a) == 0
    assert model.pending_index_of(b) == 1
    assert model.pending_index_of(c) == 2

    # Accept "b": widget removes it from the model after endpoint.accept().
    model.remove(b)

    assert model.pending_index_of(a) == 0
    assert model.pending_index_of(c) == 1
    assert model.entry_for_pending_index(0) is a
    assert model.entry_for_pending_index(1) is c
    assert model.entry_for_pending_index(2) is None


def test_pending_index_of_skips_dismissed_and_auto_entries():
    model = BusCardModel()
    a = model.add_pending("a", "1")
    auto = model.add_auto("x", "y")
    b = model.add_pending("b", "2")
    model.mark_dismissed(a)

    assert model.pending_index_of(a) is None
    assert model.pending_index_of(auto) is None
    assert model.pending_index_of(b) == 0
    assert model.entry_for_pending_index(0) is b


def test_take_pending_finds_and_removes_fifo_match():
    """Used when an "auto" policy flush resolves a message that already had
    a pending card (queued while busy, then auto-submitted on completion)."""
    model = BusCardModel()
    a = model.add_pending("observer", "hi")
    taken = model.take_pending("observer", "hi")
    assert taken is a
    assert a not in model.entries
    assert model.pending_count() == 0


def test_take_pending_no_match_returns_none():
    model = BusCardModel()
    model.add_pending("a", "1")
    assert model.take_pending("b", "2") is None


def test_rebuild_from_pending_drops_auto_and_dismissed_history():
    """Conversation reset: the model should be re-seeded from whatever is
    still queued in the endpoint, discarding auto/dismissed transcript
    history from the old conversation entirely."""
    model = BusCardModel()
    model.add_auto("x", "old auto message")
    stale_pending = model.add_pending("y", "old pending")
    model.mark_dismissed(stale_pending)

    new_entries = model.rebuild_from_pending(
        [("observer", "beam soft"), ("watchdog", "check temp")]
    )

    assert len(model.entries) == 2
    assert model.pending_count() == 2
    assert [e.sender for e in model.entries] == ["observer", "watchdog"]
    assert all(e.state == "pending" for e in model.entries)
    assert new_entries == model.entries


def test_rebuild_from_pending_empty_clears_model():
    model = BusCardModel()
    model.add_pending("a", "1")
    model.add_auto("b", "2")
    result = model.rebuild_from_pending([])
    assert result == []
    assert model.entries == []
    assert model.pending_count() == 0


def test_rebuild_from_pending_preserves_endpoint_accept_index():
    """After a reset re-render, the model's pending indices must still line
    up with ClaudeSessionEndpoint._pending so accept(0)/dismiss(0) route to
    the right message."""
    submitted = []
    endpoint = ClaudeSessionEndpoint(
        "test-agent",
        "desc",
        submit=lambda prompt: submitted.append(prompt) or True,
        is_busy=lambda: False,
        on_queued=lambda sender, message: None,
        policy="queue",
    )
    endpoint.deliver("observer", "beam soft")
    endpoint.deliver("watchdog", "check temp")

    model = BusCardModel()
    entries = model.rebuild_from_pending(endpoint.pending())
    assert model.pending_index_of(entries[0]) == 0
    assert model.pending_index_of(entries[1]) == 1

    assert endpoint.accept(0) is True
    assert submitted == [
        "[Message from agent 'observer']\nbeam soft",
    ]
    assert endpoint.pending() == [("watchdog", "check temp")]
