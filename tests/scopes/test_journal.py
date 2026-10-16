"""WriteJournal: last-write-wins offline queue."""
from __future__ import annotations

from lightfall.scopes.journal import WriteJournal, journal_path


def test_last_write_wins_and_persists(tmp_path):
    j = WriteJournal(tmp_path / "j.json")
    j.record("theme", "set", "dark")
    j.record("theme", "set", "light")
    j.record("font_size", "delete")
    again = WriteJournal(tmp_path / "j.json")
    assert again.entries() == {"theme": {"op": "set", "value": "light"},
                               "font_size": {"op": "delete", "value": None}}
    again.discard("theme")
    assert list(WriteJournal(tmp_path / "j.json").entries()) == ["font_size"]


def test_corrupt_or_foreign_entries_are_ignored(tmp_path):
    p = tmp_path / "j.json"
    p.write_text('{"a": {"op": "set", "value": 1}, "b": {"op": "explode"}, "c": 3}', encoding="utf-8")
    assert WriteJournal(p).entries() == {"a": {"op": "set", "value": 1}}
    p.write_text("{nope", encoding="utf-8")
    assert len(WriteJournal(p)) == 0


def test_unwritable_journal_keeps_entries_in_memory(tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x", encoding="utf-8")
    j = WriteJournal(blocker / "j.json")
    j.record("theme", "set", "dark")
    assert j.entries() == {"theme": {"op": "set", "value": "dark"}}


def test_journal_path_is_per_user_and_windows_safe(tmp_path):
    assert journal_path(tmp_path, "a/b").name == "journal-a_b.json"
