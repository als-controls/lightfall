"""The logbook must not rebuild unchanged fragment widgets on every refresh.

Live scans used to stall the GUI: each run document and each sync pull
re-created every fragment widget of the open entry. Refreshes are now
incremental (EntryWidget) and debounced (LogbookPanel).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from lightfall.logbook import fragment_widgets as fw
from lightfall.logbook.entry_widget import EntryData, EntryWidget
from lightfall.logbook.fragment_widgets import (
    CollapsibleGroup,
    FragmentData,
    FragmentType,
    ReadonlyFragmentWidget,
    TextFragmentWidget,
)


def _ro(i: int, *, fid: str | None = None, content: str | None = None) -> FragmentData:
    return FragmentData(
        id=fid or f"ro-{i}",
        fragment_type=FragmentType.READONLY,
        subtype="device_change",
        content=content or f"m: {i} -> {i + 1}",
        metadata={"device_name": "m", "old_value": str(i), "new_value": str(i + 1)},
    )


def _text(fid: str, content: str) -> FragmentData:
    return FragmentData(id=fid, fragment_type=FragmentType.TEXT, content=content)


def _copy(frags: list[FragmentData]) -> list[FragmentData]:
    """Fresh objects, as LogbookPanel._select_entry builds from the DB."""
    return [
        FragmentData(id=f.id, fragment_type=f.fragment_type, content=f.content,
                     subtype=f.subtype, metadata=dict(f.metadata))
        for f in frags
    ]


@pytest.fixture
def count_cards(monkeypatch):
    built = {"n": 0}
    orig = fw.ReadonlyFragmentWidget.__init__

    def counting(self, *a, **kw):
        built["n"] += 1
        orig(self, *a, **kw)

    monkeypatch.setattr(fw.ReadonlyFragmentWidget, "__init__", counting)
    return built


@pytest.fixture
def widget(qtbot):
    frags = [_text("t1", "hello"), *(_ro(i) for i in range(5))]
    w = EntryWidget(EntryData(id="e1", title="T", fragments=frags))
    qtbot.addWidget(w)
    w.show()
    return w


def test_unchanged_refresh_builds_nothing(widget, count_cards):
    items = list(widget._widget_items)
    widget.set_entry(EntryData(id="e1", title="T", fragments=_copy(widget.entry.fragments)))
    assert count_cards["n"] == 0
    assert widget._widget_items == items
    assert all(a is b for a, b in zip(widget._widget_items, items, strict=True))


def test_appended_fragment_builds_only_the_new_card(widget, count_cards):
    group = next(w for w in widget._widget_items if isinstance(w, CollapsibleGroup))
    cards = list(group._fragment_widgets)
    frags = _copy(widget.entry.fragments) + [_ro(99)]

    widget.set_entry(EntryData(id="e1", title="T", fragments=frags))

    assert count_cards["n"] == 1
    assert next(w for w in widget._widget_items if isinstance(w, CollapsibleGroup)) is group
    assert group._fragment_widgets[:5] == cards
    assert [f.id for f in widget.entry.fragments][-1] == "ro-99"


def test_changed_readonly_fragment_rerenders_in_place(widget, count_cards):
    group = next(w for w in widget._widget_items if isinstance(w, CollapsibleGroup))
    card = group._fragment_widgets[2]
    frags = _copy(widget.entry.fragments)
    frags[3].metadata = {"device_name": "m", "old_value": "2", "new_value": "42"}

    widget.set_entry(EntryData(id="e1", title="T", fragments=frags))

    assert count_cards["n"] == 0
    assert group._fragment_widgets[2] is card
    assert "42" in card._label.text()


def test_expanded_group_stays_expanded(widget):
    group = next(w for w in widget._widget_items if isinstance(w, CollapsibleGroup))
    group._collapsed = False
    group._apply_collapsed_state(animate=False)

    widget.set_entry(EntryData(id="e1", title="T", fragments=_copy(widget.entry.fragments) + [_ro(7)]))

    assert not group.is_collapsed
    assert len(group.fragments) == 6


def test_text_being_edited_is_not_overwritten(widget):
    text = next(w for w in widget._widget_items if isinstance(w, TextFragmentWidget))
    text._enter_edit_mode()
    text._editor.setPlainText("typing in progress")
    frags = _copy(widget.entry.fragments)
    frags[0].content = "stale server copy"

    widget.set_entry(EntryData(id="e1", title="T", fragments=frags + [_ro(50)]))

    assert next(w for w in widget._widget_items if isinstance(w, TextFragmentWidget)) is text
    assert text._editor.toPlainText() == "typing in progress"
    # The entry's fragment list points at the widget's object, not the stale copy.
    assert widget.entry.fragments[0] is text.fragment
    assert widget.entry.fragments[0].content == "typing in progress"


def test_text_not_being_edited_updates_in_place(widget):
    text = next(w for w in widget._widget_items if isinstance(w, TextFragmentWidget))
    frags = _copy(widget.entry.fragments)
    frags[0].content = "edited elsewhere"
    widget.set_entry(EntryData(id="e1", title="T", fragments=frags))
    assert next(w for w in widget._widget_items if isinstance(w, TextFragmentWidget)) is text
    assert text.fragment.content == "edited elsewhere"
    assert "edited elsewhere" in text._preview.text()


def test_removed_fragment_widget_is_dropped(widget):
    frags = [f for f in _copy(widget.entry.fragments) if f.id != "t1"]
    widget.set_entry(EntryData(id="e1", title="T", fragments=frags))
    assert not any(isinstance(w, TextFragmentWidget) for w in widget._widget_items)


def test_switching_entry_builds_fresh_widgets(widget):
    old = list(widget._widget_items)
    widget.set_entry(EntryData(id="e2", title="Other", fragments=_copy(widget.entry.fragments)))
    assert not any(a is b for a, b in zip(widget._widget_items, old, strict=False))


# -- LogbookPanel: debounce + skip unchanged -----------------------------------


@pytest.fixture
def panel(qtbot, tmp_path: Path, monkeypatch):
    from lightfall.logbook.client import LogbookClient
    from lightfall.ui.panels.logbook_panel import LogbookPanel

    client = LogbookClient()
    client._db_path = tmp_path / "logbook.db"
    monkeypatch.setattr(client, "_load_preferences", lambda: None)
    client._offline_only = True
    client.init()
    monkeypatch.setattr(LogbookClient, "_instance", client)
    logbook_id = client.get_or_create_logbook("tester")
    older = client.create_entry(logbook_id, title="older")
    for i in range(4):
        client.add_fragment(older, kind="readonly", subtype="device_change",
                            content=f"m {i}", data={"device_name": "m", "new_value": str(i)})
    client._db.execute("UPDATE entry SET created_at = '2000-01-01' WHERE id = ?", (older,))
    client._db.commit()
    newer = client.create_entry(logbook_id, title="newer")

    monkeypatch.setattr(LogbookPanel, "_deferred_init", lambda self: None)
    p = LogbookPanel()
    qtbot.addWidget(p)
    p._client = client
    p._logbook_id = logbook_id
    p._load_entries()
    p.entry_ids = {"older": older, "newer": newer}
    yield p
    client._db.close()


def _count_calls(monkeypatch, obj, name):
    calls = []
    orig = getattr(obj, name)

    def wrapper(*a, **kw):
        calls.append(a)
        return orig(*a, **kw)

    monkeypatch.setattr(obj, name, wrapper)
    return calls


def test_burst_of_injections_coalesces_into_one_refresh(panel, qtbot, monkeypatch):
    older = panel.entry_ids["older"]
    panel._select_entry(older)
    set_entry_calls = _count_calls(monkeypatch, panel._entry_widget, "set_entry")

    for i in range(10):
        panel._client.add_fragment(older, kind="readonly", subtype="device_change",
                                   content=f"burst {i}", data={"n": i})
        panel._on_fragment_injected(older)
    assert set_entry_calls == []  # nothing synchronous

    qtbot.waitUntil(lambda: len(set_entry_calls) == 1, timeout=2000)
    qtbot.wait(panel.REFRESH_DEBOUNCE_MS * 2)
    assert len(set_entry_calls) == 1
    assert len(panel._entry_widget.entry.fragments) == 14


def test_select_entry_skips_unchanged_content(panel, monkeypatch):
    older = panel.entry_ids["older"]
    panel._select_entry(older)
    set_entry_calls = _count_calls(monkeypatch, panel._entry_widget, "set_entry")
    panel._select_entry(older)
    assert set_entry_calls == []
    panel._client.add_fragment(older, kind="readonly", subtype="device_change", content="new")
    panel._select_entry(older)
    assert len(set_entry_calls) == 1


def test_pull_keeps_the_open_entry_selected(panel, qtbot):
    older = panel.entry_ids["older"]
    assert panel._current_entry_id == panel.entry_ids["newer"]  # newest first
    panel._select_entry(older)

    panel._on_pull()
    qtbot.wait(panel.REFRESH_DEBOUNCE_MS * 3)

    assert panel._current_entry_id == older
    assert panel._entry_widget.entry.id == older


def test_pull_shows_fragments_added_remotely(panel, qtbot):
    older = panel.entry_ids["older"]
    panel._select_entry(older)
    fid = str(uuid.uuid4())
    panel._client.add_fragment(older, kind="readonly", subtype="device_change",
                               content="from server", fragment_id=fid)
    panel._on_pull()
    qtbot.waitUntil(lambda: any(f.id == fid for f in panel._entry_widget.entry.fragments), timeout=2000)
    assert isinstance(
        next(w for w in panel._entry_widget._widget_items if isinstance(w, CollapsibleGroup)),
        CollapsibleGroup,
    )
    assert all(isinstance(c, ReadonlyFragmentWidget)
               for w in panel._entry_widget._widget_items if isinstance(w, CollapsibleGroup)
               for c in w._fragment_widgets)


def test_pending_image_loads_on_refresh_without_row_changes(qtbot, tmp_path, monkeypatch):
    """Skipping an unchanged entry must still pick up images a sync downloaded."""
    from PySide6.QtGui import QColor, QPixmap

    from lightfall.logbook.client import LogbookClient

    png = tmp_path / "img.png"
    state = {"path": None}

    class _Stub:
        def _get_local_image_path(self, image_id):
            return state["path"]

    monkeypatch.setattr(LogbookClient, "get_instance", classmethod(lambda cls: _Stub()))
    img = FragmentData(id="i1", fragment_type=FragmentType.IMAGE, content="cap",
                       metadata={"image_id": "img-1"})
    w = EntryWidget(EntryData(id="e1", fragments=[img]))
    qtbot.addWidget(w)
    image_widget = w._widget_items[0]
    assert image_widget.is_pending

    pm = QPixmap(8, 8)
    pm.fill(QColor("red"))
    pm.save(str(png))
    state["path"] = png
    w.refresh_pending_images()
    assert not image_widget.is_pending
