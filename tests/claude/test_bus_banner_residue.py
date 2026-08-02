from __future__ import annotations

from lightfall.claude.bus_endpoint import ClaudeSessionEndpoint, bus_banner_text


def test_bus_banner_text_empty_hides():
    assert bus_banner_text([]) is None


def test_bus_banner_text_singular():
    assert bus_banner_text([("observer", "hi")]) == "1 message from observer"


def test_bus_banner_text_plural():
    pending = [("a", "1"), ("b", "2"), ("c", "3")]
    assert bus_banner_text(pending) == "3 pending agent messages"


def test_respond_flush_partial_leaves_residue_visible():
    """Mirrors _on_bus_respond_clicked: flush submits only the first message
    (agent goes busy partway through), remainder re-queues -- the banner
    decision helper must report "show residual", not "hide"."""
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
    ep.flush_pending()

    # Simulates the widget's _refresh_bus_banner() decision post-flush.
    text = bus_banner_text(ep.pending())
    assert text is not None
    assert "1 pending" in text or "1 message from b" in text
    assert ep.pending() == [("b", "2")]


def test_respond_flush_already_busy_leaves_all_pending_visible():
    """Zero submitted (already busy) -> banner must still show, not hide."""
    ep = ClaudeSessionEndpoint(
        "lightfall", "main agent",
        submit=lambda text: False,
        is_busy=lambda: True,
        on_queued=lambda s, m: None,
        policy="queue",
    )
    ep.deliver("a", "1")
    ep.flush_pending()

    assert bus_banner_text(ep.pending()) == "1 message from a"


def test_respond_flush_full_success_hides_banner():
    ep = ClaudeSessionEndpoint(
        "lightfall", "main agent",
        submit=lambda text: True,
        is_busy=lambda: True,
        on_queued=lambda s, m: None,
        policy="queue",
    )
    ep.deliver("a", "1")
    ep.flush_pending()

    assert bus_banner_text(ep.pending()) is None


def test_submit_refuses_when_draft_present_message_stays_pending():
    """Mirrors the widget-level guard in _submit_bus_prompt: a non-empty
    draft in the input field must make submit() return False, so the
    endpoint re-queues the message instead of clobbering the user's text."""

    def submit_with_draft_guard(draft_text: str):
        def submit(_prompt: str) -> bool:
            if draft_text.strip():
                return False
            return True
        return submit

    ep = ClaudeSessionEndpoint(
        "lightfall", "main agent",
        submit=submit_with_draft_guard("half-typed question"),
        is_busy=lambda: False,
        on_queued=lambda s, m: None,
        policy="auto",
    )
    assert ep.deliver("observer", "beam soft") == "queued"
    assert ep.pending() == [("observer", "beam soft")]
