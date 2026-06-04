"""TaskCard renders status, description, and counters."""
from __future__ import annotations

from lightfall.claude.widgets.task_card import TaskCard


def test_initial_state_is_running(qtbot):
    card = TaskCard("t1", "investigating widget tree")
    qtbot.addWidget(card)
    assert card.task_id == "t1"
    assert card._status == "running"
    assert "investigating widget tree" in card.title_label.text()


def test_update_progress_refreshes_counters(qtbot):
    card = TaskCard("t1", "investigating")
    qtbot.addWidget(card)
    card.update_progress(
        "investigating",
        {"total_tokens": 12345, "tool_uses": 7, "duration_ms": 100},
        "Read",
    )
    # 12,345 should be formatted with a thousands separator.
    assert "12,345" in card.counter_label.text()
    assert "7 tools" in card.counter_label.text()


def test_mark_finished_sets_status_and_summary(qtbot):
    card = TaskCard("t1", "investigating")
    qtbot.addWidget(card)
    card.mark_finished(
        status="completed",
        summary="Found 3 widgets",
        output_file="/tmp/x.jsonl",
        usage={"total_tokens": 2000, "tool_uses": 5, "duration_ms": 200},
    )
    assert card._status == "completed"
    assert "Found 3 widgets" in card.detail_summary.text()
    assert "/tmp/x.jsonl" in card.output_link.text()


def test_unknown_status_falls_back_to_completed(qtbot):
    card = TaskCard("t1", "x")
    qtbot.addWidget(card)
    card.mark_finished("bogus", "", "", {})
    assert card._status == "completed"


def test_summary_renders_as_plaintext_not_html(qtbot):
    """Subagent summary may contain '<' or other HTML-looking chars —
    must render literally."""
    from PySide6.QtCore import Qt

    card = TaskCard("t1", "investigating")
    qtbot.addWidget(card)
    card.mark_finished("completed", "<b>NOT BOLD</b>", "", {})
    assert card.detail_summary.textFormat() == Qt.TextFormat.PlainText
    assert card.detail_summary.text() == "<b>NOT BOLD</b>"


def test_output_file_path_is_escaped_in_link(qtbot):
    """If output_file contained < or > the anchor markup must not break."""
    card = TaskCard("t1", "investigating")
    qtbot.addWidget(card)
    card.mark_finished("completed", "", "/tmp/<weird>.jsonl", {})
    text = card.output_link.text()
    assert "/tmp/&lt;weird&gt;.jsonl" in text
    assert "/tmp/<weird>.jsonl" not in text
